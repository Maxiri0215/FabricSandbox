"""HTTP-friendly lifecycle for the project's original live loopback TCP Agent.

No GPU or switch is emulated. Three independent Python subprocesses provide
receiver/A/B endpoints, and actual bytes travel over localhost sockets. The
original Controller and TCPAdapter remain byte-for-byte unchanged in vendor.
"""
from __future__ import annotations

import atexit
import copy
import json
from pathlib import Path
import sys
import threading
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parent
VENDOR = ROOT / "vendor"
if str(VENDOR) not in sys.path:
    sys.path.insert(0, str(VENDOR))

from agent_demo.common import atomic_json, read_json
from agent_demo.controller import Controller
from agent_demo.tcp_backend import TCPAdapter

BOUNDARY = "本机真实 TCP 子进程与应用层通道切换；不是 GPU、物理交换机或 RDMA 实测。"
STAGE_NAMES = ["输入与初始方案", "真实异常", "诊断证据", "候选及拒绝理由", "所选方案", "执行过程", "新进度与验证结果"]


class TCPDemoSession:
    """One bounded, explicitly-owned demo session; safe for threaded HTTP reads.

    start() -> snapshot; inject(kind='channel_a') -> snapshot (nonblocking);
    snapshot() -> dict; close() waits for cleanup and returns final snapshot.
    A finished recovery keeps transferring on B until close or max_seconds.
    """

    def __init__(self, output_root: Path | str | None = None, *, max_seconds: float = 300,
                 transfer_interval: float = 0.25, timeout: float = 0.5):
        if not 5 <= max_seconds <= 600:
            raise ValueError("max_seconds must be between 5 and 600")
        if not 0.05 <= transfer_interval <= 5:
            raise ValueError("transfer_interval must be between 0.05 and 5")
        self.session_id = "tcp-" + uuid4().hex[:16]
        self.output = (Path(output_root) if output_root else ROOT / "runtime/tcp") / self.session_id
        self.max_seconds, self.interval, self.timeout = max_seconds, transfer_interval, timeout
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._inject_requested = threading.Event()
        self._thread = None
        self._adapter = None
        self._status = "idle"
        self._snapshot = None
        self._baseline = None
        self._last_observation = None
        self._truth = None
        self._error = None
        self._events = []
        self._initial_processes = {}
        self._kind = "channel_a"
        self._started_at = None
        self._deadline = None
        self._closed_at = None
        self._cleanup_complete = False
        self._terminal_status = None
        self._close_reason = None
        self._result = None

    def _event(self, kind, message, evidence=None):
        row = {"kind": kind, "timestamp": time.time(), "message": message,
               "evidence": evidence or {}}
        with self._lock:
            self._events.append(row)
        if self.output.exists():
            with (self.output / "session_events.jsonl").open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")

    def start(self):
        with self._lock:
            if self._status != "idle":
                raise RuntimeError("A session may only be started once")
            self._status = "running"
            self._started_at = time.time()
            self._deadline = time.monotonic() + self.max_seconds
        self.output.mkdir(parents=True, exist_ok=False)
        adapter = TCPAdapter(self.output / "backend", timeout=self.timeout)
        self._adapter = adapter
        try:
            snapshot = adapter.start()
            baseline = adapter.baseline()
            if not baseline.get("recovered"):
                raise RuntimeError("Healthy baseline failed its checksum/ACK verification")
            with self._lock:
                self._snapshot, self._baseline = snapshot, baseline
                self._initial_processes = {k: p.pid for k, p in adapter.processes.items()}
            self._event("session_started", "独立 receiver、A、B 进程已监听；初始经 A 传输。", snapshot)
            self._event("baseline_verified", "两块 64 KiB 数据已通过 SHA256 与 ACK 验证。", baseline)
            self._thread = threading.Thread(target=self._run, name=self.session_id, daemon=True)
            self._thread.start()
            atexit.register(self.close)
        except Exception as exc:
            with self._lock:
                self._status = "failed"
                self._error = {"type": type(exc).__name__, "message": str(exc)}
            self._event("startup_failed", "启动或基线验证失败，未产生成功实测。", self._error)
            self._cleanup()
        return self.snapshot()

    def inject(self, kind="channel_a"):
        # Deliberately no arbitrary PIDs, signals, hosts, ports or shell commands.
        if kind not in {"channel_a", "all_channels", "receiver"}:
            raise ValueError("Allowed faults: channel_a, all_channels, receiver")
        with self._lock:
            if self._status != "running" or self._truth or self._inject_requested.is_set():
                raise RuntimeError("Start a healthy new session before injecting one fault")
            self._kind = kind
            self._status = "recovering"
            self._inject_requested.set()
        self._event("injection_requested", "已请求终止本会话登记的故障目标，后台执行。", {"kind": kind})
        return self.snapshot()

    def _run(self):
        try:
            while not self._stop.is_set() and time.monotonic() < self._deadline:
                if self._inject_requested.is_set():
                    self._inject_requested.clear()
                    self._recover_once()
                    if self._stop.is_set():
                        break
                with self._lock:
                    status = self._status
                if status in {"running", "recovered"}:
                    observation = self._adapter.observe()
                    with self._lock:
                        self._last_observation = observation
                    if observation.get("status") != "healthy":
                        # Unexpected external interference is reported, never an invented repair.
                        with self._lock:
                            self._status = "failed"
                            self._error = {"type": "UnexpectedTransferFailure",
                                           "message": "Data transfer failed outside the controlled recovery"}
                        self._event("transfer_failed", "当前路径未能完成实际数据传输。", observation)
                        break
                if self._stop.wait(self.interval):
                    break
            if not self._stop.is_set() and time.monotonic() >= self._deadline:
                self._close_reason = "session_time_limit"
                self._event("time_limit", "会话达到运行期限，清理本会话子进程。")
                with self._lock:
                    self._terminal_status = self._status
                    self._status = "stopped"
        except Exception as exc:
            with self._lock:
                self._status = "failed"
                self._error = {"type": type(exc).__name__, "message": str(exc)}
            self._event("session_failed", "真实执行发生错误。", self._error)
        finally:
            self._cleanup()

    def _recover_once(self):
        if self._stop.is_set():
            return
        before = {k: {"pid": p.pid, "returncode": p.poll()}
                  for k, p in self._adapter.processes.items()}
        truth = self._adapter.inject(self._kind)
        after = {k: {"pid": p.pid, "returncode": p.poll()}
                 for k, p in self._adapter.processes.items()}
        with self._lock:
            self._truth = {**truth, "processes_before": before, "processes_after": after}
        self._event("process_terminated", "故障目标进程已经退出；Agent 随后独立探测通道和接收端。", self._truth)
        if self._stop.is_set():
            return
        # The original Controller receives only the adapter. Injection truth is not passed.
        controller = Controller(self._adapter, self.output / "agent", mode="rules", verbose=False)
        incident = controller.run(watch_timeout=10)
        mapped = {"recovered": "recovered", "blocked": "blocked", "inconclusive": "failed"}
        with self._lock:
            self._result = copy.deepcopy(incident)
            self._terminal_status = incident["status"]
            self._status = mapped.get(incident["status"], "failed")
        self._event("agent_finished", {"recovered": "规则 Agent 已切换通道并确认重试块和新数据块。",
                    "blocked": "没有可用候选，Agent 返回 blocked，未执行切换。",
                    "inconclusive": "接收端也不可达，证据不足以隔离转发通道故障。"}.get(
                        incident["status"], "Agent 未能验证恢复成功。"),
                    {"status": incident["status"], "tool_calls": incident.get("tool_calls"),
                     "verification": incident.get("verification")})
        atomic_json(self.output / "result.json", {
            "source": "MEASURED", "mode": "live_local_tcp", "boundary": BOUNDARY,
            "session_id": self.session_id, "snapshot": self._snapshot,
            "baseline": self._baseline, "incident": incident,
            "status": incident["status"], "ground_truth_file": "backend/ground_truth.json"})

    def _cleanup(self):
        adapter = self._adapter
        if adapter is not None:
            try:
                adapter.close()
            except Exception as exc:
                with self._lock:
                    self._error = {"type": type(exc).__name__, "message": "Cleanup: " + str(exc)}
                    self._status = "failed"
        with self._lock:
            self._cleanup_complete = not adapter or all(p.poll() is not None for p in adapter.processes.values())
            self._closed_at = time.time()
        if self.output.exists():
            atomic_json(self.output / "cleanup.json", {
                "complete": self._cleanup_complete, "closed_at": self._closed_at,
                "reason": self._close_reason, "processes": self._processes()})

    def _processes(self):
        adapter = self._adapter
        if not adapter:
            return {}
        return {name: {"pid": process.pid, "port": adapter.endpoints.get(name),
                       "host": "127.0.0.1", "alive": process.poll() is None,
                       "returncode": process.poll()}
                for name, process in list(adapter.processes.items())}

    def close(self):
        with self._lock:
            if self._cleanup_complete:
                return self.snapshot()
            self._close_reason = self._close_reason or "user_or_server_stop"
            self._stop.set()
        thread = self._thread
        if thread and thread is not threading.current_thread():
            # Socket operations are bounded (0.5s each); the original executor's
            # terminate/wait/kill operations are also bounded and owned-Popen only.
            thread.join(timeout=30)
            if thread.is_alive():
                with self._lock:
                    self._status = "failed"
                    self._error = {"type": "CleanupTimeout", "message": "Session worker did not stop in 30 seconds"}
                return self.snapshot()
        elif not thread:
            self._cleanup()
        with self._lock:
            if self._status not in {"failed", "stopped"}:
                self._terminal_status = self._terminal_status or self._status
                self._status = "stopped"
        try:
            atexit.unregister(self.close)
        except Exception:
            pass
        return self.snapshot()

    def _stages(self, incident):
        events = incident.get("events", [])
        results = {e["tool"]: e.get("result", {}) for e in events if e.get("kind") == "tool_result"}
        starts = [e.get("tool") for e in events if e.get("kind") == "tool_start"]
        selection = next((e for e in events if e.get("kind") == "selection"), None)
        diagnosis = incident.get("diagnosis") or results.get("diagnose")
        rounds = incident.get("candidate_rounds", [])
        candidates = rounds[-1] if rounds else None
        selected = incident.get("selected_plan")
        if not selected and selection and candidates:
            selected = next((r["candidate"] for r in candidates
                             if r["candidate"]["id"] == selection.get("candidate_id")), None)
        receipt = incident.get("attempts", [{}])[-1].get("receipt") if incident.get("attempts") else None
        verification = incident.get("verification")
        obs = incident.get("observation") or next((e.get("observation") for e in events
                if e.get("kind") == "anomaly_detected"), None)
        facts = [self._snapshot, {"experiment_injection": self._truth, "independent_observation": obs}
                 if self._truth else None, diagnosis, candidates,
                 {"selection_event": selection, "candidate": selected, "validation": results.get("validate")}
                 if selection else None, receipt, verification]
        stages = []
        for i, title in enumerate(STAGE_NAMES):
            state = "done" if facts[i] else "pending"
            if i == 6 and verification and not verification.get("recovered"):
                state = "error"
            if i == 3 and candidates and not any(r["feasible"] for r in candidates):
                state = "error"
            stages.append({"index": i + 1, "title": title, "state": state, "facts": facts[i]})
        if self._status == "recovering":
            tool_stage = {"observe": 2, "diagnose": 3, "propose": 4,
                          "validate": 5, "apply": 6, "verify": 7}
            active = tool_stage.get(starts[-1], 2) if starts else 2
            if stages[active - 1]["state"] == "pending":
                stages[active - 1]["state"] = "active"
        if incident.get("status") == "inconclusive":
            stages[2]["state"] = "error"
        return stages

    def snapshot(self):
        # Read the original atomic incident file instead of traversing a record
        # that a Controller thread may currently be mutating.
        incident = read_json(self.output / "agent/incident.json", {})
        with self._lock:
            processes = self._processes()
            receiver = read_json(self.output / "backend/receiver_state.json", {})
            a = self._adapter
            metrics = {"acknowledged_blocks": len(a.acknowledged_ids) if a else 0,
                       "acknowledged_bytes": len(a.acknowledged_ids) * 65536 if a else 0,
                       "current_route": a.active if a else "A", "failed_blocks": a.failed_attempts if a else 0,
                       "failed_attempts": a.failed_attempts if a else 0,
                       "attempts": a.attempts if a else 0, "receiver_unique_blocks": receiver.get("unique_chunks", 0),
                       "receiver_unique_bytes": receiver.get("unique_bytes", 0),
                       "duplicate_requests": receiver.get("duplicate_requests", 0),
                       "version": a.version if a else 0,
                       "metric_boundary": "失败数统计传输尝试，重试成功不等于永久丢块。探针不计入数据计数。"}
            useful = []
            for e in incident.get("events", []):
                if e.get("kind") in {"tool_result", "selection", "terminal", "anomaly_detected"}:
                    useful.append({"timestamp": e.get("wall_time"), "kind": e["kind"],
                                   "message": {"tool_result": "工具返回 " + e.get("tool", ""),
                                               "selection": "选择候选 " + e.get("candidate_id", ""),
                                               "terminal": "Agent 终态 " + e.get("status", ""),
                                               "anomaly_detected": "独立探测发现异常"}[e["kind"]],
                                   "evidence": e})
            event_rows = sorted(copy.deepcopy(self._events) + useful, key=lambda e: e.get("timestamp", 0))
            return {"session_id": self.session_id, "mode": "live_local_tcp", "source": "MEASURED" if self._baseline else "ATTEMPTED",
                    "status": self._status, "terminal_status": self._terminal_status,
                    "boundary": BOUNDARY, "effective_mode": "rules", "started_at": self._started_at,
                    "updated_at": time.time(), "closed_at": self._closed_at,
                    "cleanup_complete": self._cleanup_complete, "time_limit_seconds": self.max_seconds,
                    "output_directory": str(self.output), "metrics": metrics,
                    "topology": {"host": "127.0.0.1", "receiver": processes.get("receiver", {}),
                                 "primary": {**processes.get("A", {}), "channel": "A"},
                                 "spare": {**processes.get("B", {}), "channel": "B"}},
                    "stages": self._stages(incident), "events": event_rows,
                    "baseline": copy.deepcopy(self._baseline), "observation": copy.deepcopy(self._last_observation),
                    "incident": incident, "error": copy.deepcopy(self._error)}


if __name__ == "__main__":
    session = TCPDemoSession()
    try:
        print(json.dumps(session.start(), ensure_ascii=False))
        if session.snapshot()["status"] == "running":
            session.inject()
            deadline = time.monotonic() + 20
            while session.snapshot()["status"] == "recovering" and time.monotonic() < deadline:
                time.sleep(0.1)
            print(json.dumps(session.snapshot(), ensure_ascii=False))
    finally:
        session.close()
