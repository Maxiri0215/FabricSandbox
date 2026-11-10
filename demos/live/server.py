#!/usr/bin/env python3
"""Local presentation console backed by the original simulator and TCP agent."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import mimetypes
from pathlib import Path
import secrets
import signal
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit
import uuid
import webbrowser

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from demo_model import default_topology, plan_case, simulate_case, validate_topology
from tcp_runner import TCPDemoSession


class Console:
    def __init__(self):
        self.token = secrets.token_urlsafe(24)
        self.started_at = time.time()
        self.lock = threading.RLock()
        self.sim_lock = threading.Lock()
        self.tcp_lock = threading.Lock()
        self.analyses: dict[str, dict] = {}
        self.latest: str | None = None
        self.tcp: TCPDemoSession | None = None
        self.evidence = json.loads((ROOT / 'data/gpu_evidence.json').read_text())

    def analyze(self, payload):
        if not self.sim_lock.acquire(blocking=False):
            raise Conflict('已有仿真正在计算，请等待结果。')
        try:
            fixed = copy.deepcopy(payload)
            result = plan_case(fixed)
            run_id = 'plan-' + uuid.uuid4().hex[:12]
            result['run_id'] = run_id
            result['mode'] = 'computed_simulation'
            result['created_at'] = time.time()
            with self.lock:
                self.analyses[run_id] = {'payload': fixed, 'analysis': result, 'execution': None}
                self.latest = run_id
                if len(self.analyses) > 20:
                    del self.analyses[next(iter(self.analyses))]
            return result
        finally:
            self.sim_lock.release()

    def apply(self, payload):
        if not self.sim_lock.acquire(blocking=False):
            raise Conflict('已有仿真正在计算，请等待结果。')
        try:
            with self.lock:
                run = self.analyses.get(payload.get('run_id'))
                if run is None:
                    raise Conflict('找不到这份输入的分析记录，请重新分析。')
                candidate_id = payload.get('candidate_id')
                candidates = run['analysis'].get('candidates', [])
                candidate = next((c for c in candidates if c.get('id') == candidate_id), None)
                if candidate is None:
                    raise ValueError('候选不属于本次分析。')
                feasible = candidate.get('admissible', candidate.get('feasible', candidate.get('accepted', False)))
                if not feasible:
                    raise ValueError('候选未通过约束校验，不能执行。')
                if not (candidate.get('metrics') or {}).get('all_jobs_completed'):
                    raise ValueError('候选在评估时域内未完成全部作业，请调整拓扑或选择已完成的候选。')
                if run['execution'] is not None:
                    if run['execution']['candidate_id'] != candidate_id:
                        raise Conflict('本次输入已采用另一候选，请重新分析后再提交。')
                    return copy.deepcopy(run['execution'])
                fixed = copy.deepcopy(run['payload'])
            fixed.update({'scenario': 'ddp', 'variant': candidate['variant']})
            evaluation = simulate_case(fixed)
            if (evaluation['input_hash'] != candidate['input_hash'] or
                    evaluation['workload_hash'] != run['analysis']['workload_hash']):
                raise Conflict('重算输入与已冻结候选不一致，请重新分析。')
            result = {'mode': 'computed_simulation', 'run_id': payload['run_id'],
                      'candidate_id': candidate_id, 'input_hash': run['analysis'].get('input_hash'),
                      'candidate': candidate, 'evaluation': evaluation, 'executed_at': time.time(),
                      'boundary': '重新运行原Python仿真内核，不会调度真实GPU。'}
            with self.lock:
                run['execution'] = result
            return result
        finally:
            self.sim_lock.release()

    def tcp_start(self):
        if not self.tcp_lock.acquire(blocking=False):
            raise Conflict('TCP实验正在启动或停止，请稍后再试。')
        try:
            if self.tcp:
                self.tcp.close()
            self.tcp = TCPDemoSession(output_root=ROOT / 'runtime/tcp', max_seconds=300)
            return self.tcp.start()
        finally:
            self.tcp_lock.release()

    def tcp_snapshot(self):
        return self.tcp.snapshot() if self.tcp else {'status': 'idle', 'mode': 'live_local_tcp', 'stages': [], 'events': [], 'metrics': {}, 'topology': {}}

    def tcp_inject(self, payload):
        if not self.tcp_lock.acquire(blocking=False):
            raise Conflict('TCP实验正在启动或停止，请稍后再试。')
        try:
            if self.tcp is None:
                raise Conflict('请先启动真实通信。')
            if payload.get('session_id') != self.tcp.session_id:
                raise Conflict('会话已变化，请刷新后使用当前会话。')
            kind = payload.get('kind', 'channel_a')
            if kind not in {'channel_a', 'all_channels'}:
                raise ValueError('只支持主通道或主备通道故障。')
            try:
                return self.tcp.inject(kind)
            except RuntimeError as exc:
                raise Conflict(str(exc)) from exc
        finally:
            self.tcp_lock.release()

    def tcp_stop(self, payload):
        with self.tcp_lock:
            if self.tcp:
                if payload.get('session_id') != self.tcp.session_id:
                    raise Conflict('会话已变化，旧页面不能停止新会话。')
                self.tcp.close()
            return self.tcp_snapshot()

    def export(self):
        with self.lock:
            run = copy.deepcopy(self.analyses.get(self.latest))
        return {'application': 'FabricSandbox Demo', 'exported_at': time.time(),
                'network': run, 'tcp': self.tcp_snapshot(),
                'recorded_gpu': {'mode': 'recorded_gpu_evidence', 'incident_id': self.evidence.get('incident_id'),
                                 'captured_at': self.evidence.get('captured_at'), 'source_manifest': 'data/gpu_sources.json'},
                'boundaries': ['网络方案由原Python仿真内核重算。', 'TCP模式使用本机真实子进程与传输。', '3090模式回放已有实测证据。']}

    def close(self):
        if self.tcp:
            self.tcp.close()


class Conflict(Exception):
    pass


def handler(console):
    class Handler(BaseHTTPRequestHandler):
        server_version = 'FabricSandboxDemo/1.0'

        def log_message(self, fmt, *args):
            if args and '/api/tcp/status' in str(args[0]):
                return
            super().log_message(fmt, *args)

        def allowed_host(self):
            return self.headers.get('Host', '') in {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}

        def respond(self, status, data, content_type='application/json; charset=utf-8'):
            raw = json.dumps(data, ensure_ascii=False, allow_nan=False).encode() if not isinstance(data, bytes) else data
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(raw)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Content-Security-Policy', "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; frame-ancestors 'none'")
            self.end_headers()
            try:
                self.wfile.write(raw)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def do_GET(self):
            if not self.allowed_host():
                return self.respond(403, {'error': '仅接受本机地址。'})
            path = urlsplit(self.path).path
            try:
                if path == '/api/health':
                    return self.respond(200, {'application': 'FabricSandboxDemo', 'version': 1, 'ok': True, 'started_at': console.started_at})
                if path == '/api/config':
                    return self.respond(200, {'token': console.token, 'application': 'FabricSandboxDemo', 'modes': ['computed_simulation', 'live_local_tcp', 'recorded_gpu_evidence']})
                if path == '/api/topology/default':
                    return self.respond(200, default_topology())
                if path == '/api/gpu-evidence':
                    return self.respond(200, console.evidence)
                if path == '/api/tcp/status':
                    return self.respond(200, console.tcp_snapshot())
                if path == '/api/export':
                    return self.respond(200, console.export())
                if path == '/favicon.ico':
                    return self.respond(204, b'', 'image/x-icon')
                assets = {'/': 'index.html', '/index.html': 'index.html', '/styles.css': 'styles.css', '/app.js': 'app.js'}
                if path not in assets:
                    return self.respond(404, {'error': '未找到页面。'})
                target = ROOT / 'web' / assets[path]
                mime = mimetypes.guess_type(target.name)[0] or 'application/octet-stream'
                return self.respond(200, target.read_bytes(), mime + '; charset=utf-8')
            except Exception as exc:
                return self.respond(500, {'error': str(exc)})

        def do_POST(self):
            if not self.allowed_host():
                return self.respond(403, {'error': '仅接受本机地址。'})
            origin = self.headers.get('Origin')
            if origin and origin not in {f'http://127.0.0.1:{self.server.server_port}', f'http://localhost:{self.server.server_port}'}:
                return self.respond(403, {'error': '来源不匹配。'})
            if not secrets.compare_digest(self.headers.get('X-Demo-Token', ''), console.token):
                return self.respond(403, {'error': '页面已失效，请刷新。'})
            if self.headers.get('Content-Type', '').split(';')[0].strip() != 'application/json':
                return self.respond(415, {'error': '请求必须为JSON。'})
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 65536:
                    raise ValueError('请求长度不合法。')
                payload = json.loads(self.rfile.read(length))
                if not isinstance(payload, dict):
                    raise ValueError('请求必须为对象。')
                path = urlsplit(self.path).path
                if path == '/api/planning/analyze':
                    result = console.analyze(payload)
                elif path == '/api/topology/validate':
                    result = validate_topology(payload)
                elif path == '/api/planning/apply':
                    result = console.apply(payload)
                elif path == '/api/tcp/start':
                    result = console.tcp_start()
                elif path == '/api/tcp/inject':
                    result = console.tcp_inject(payload)
                elif path == '/api/tcp/stop':
                    result = console.tcp_stop(payload)
                else:
                    return self.respond(404, {'error': '未找到操作。'})
                return self.respond(200, result)
            except Conflict as exc:
                return self.respond(409, {'error': str(exc)})
            except (ValueError, KeyError, TypeError) as exc:
                return self.respond(400, {'error': str(exc)})
            except Exception as exc:
                return self.respond(500, {'error': str(exc)})
    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--open', action='store_true')
    args = parser.parse_args()
    if not 0 <= args.port <= 65535:
        parser.error('port must be 0..65535')
    console = Console()
    try:
        server = ThreadingHTTPServer(('127.0.0.1', args.port), handler(console))
    except OSError as exc:
        console.close()
        print(f'无法绑定端口 {args.port}: {exc}\n可尝试 python3 server.py --port 0 --open', file=sys.stderr)
        raise SystemExit(1)
    server.daemon_threads = True
    url = f'http://127.0.0.1:{server.server_port}'
    runtime = ROOT / 'runtime'; runtime.mkdir(exist_ok=True)
    (runtime / 'server.json').write_text(json.dumps({'url': url, 'started_at': console.started_at}))
    print(f'FabricSandbox 演示已启动：{url}', flush=True)
    print('全程本机运行。按 Ctrl+C 停止服务并清理本次TCP子进程。', flush=True)
    if args.open:
        threading.Timer(0.3, lambda: webbrowser.open(url)).start()
    def stop(*_):
        threading.Thread(target=server.shutdown, daemon=True).start()
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        server.serve_forever(poll_interval=0.2)
    finally:
        console.close()
        server.server_close()


if __name__ == '__main__':
    main()
