"""Real localhost HTTP integration. Owns an ephemeral-port server and TCP children."""
from __future__ import annotations
import json
from pathlib import Path
import queue
import re
import subprocess
import sys
import threading
import time
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]


class HTTPDemoTest(unittest.TestCase):
    def setUp(self):
        self.audit = {"test": "actual localhost HTTP integration", "started_at": time.time(),
                      "python": sys.version, "checks": [], "cleanup": {}}
        self.process = self.url = self.token = self.tcp_session_id = None
        self.lines, self.tcp_started = [], False
        self.addCleanup(self.cleanup_server)
        ready = queue.Queue()
        self.process = subprocess.Popen([sys.executable, "-u", str(ROOT / "server.py"), "--port", "0"],
            cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        def reader():
            for line in self.process.stdout:
                self.lines.append(line.rstrip())
                match = re.search(r"http://127\.0\.0\.1:\d+", line)
                if match:
                    ready.put(match.group())
        self.reader = threading.Thread(target=reader, daemon=True)
        self.reader.start()
        self.url = ready.get(timeout=15)
        runtime = json.loads((ROOT / "runtime/server.json").read_text())
        self.assertEqual(runtime["url"], self.url)
        self.assertGreaterEqual(runtime["started_at"], self.audit["started_at"])
        code, health = self.http("GET", "/api/health")
        self.assertEqual(code, 200)
        self.assertEqual(health["started_at"], runtime["started_at"])
        self.audit["server"] = {**runtime, "pid": self.process.pid}
        code, config = self.http("GET", "/api/config")
        self.assertEqual(code, 200)
        self.token = config["token"]
        self.mark("owned_ephemeral_server", {"runtime_and_health_timestamp_match": True})

    def mark(self, name, detail=None):
        self.audit["checks"].append({"name": name, "passed": True, "detail": detail or {}})

    def http(self, method, path, payload=None, *, token=True, origin=None):
        headers, data = {}, None
        if method == "POST":
            headers["Content-Type"] = "application/json"
            if token and self.token:
                headers["X-Demo-Token"] = self.token
            if origin:
                headers["Origin"] = origin
            data = json.dumps(payload if payload is not None else {}).encode()
        try:
            with urlopen(Request(self.url + path, data=data, headers=headers, method=method), timeout=35) as response:
                return response.status, json.loads(response.read())
        except HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def until_tcp(self, predicate):
        deadline = time.monotonic() + 20
        latest = {}
        while time.monotonic() < deadline:
            code, latest = self.http("GET", "/api/tcp/status")
            self.assertEqual(code, 200)
            if predicate(latest):
                return latest
            self.assertNotEqual(latest["status"], "failed", latest.get("error"))
            time.sleep(.08)
        self.fail("TCP timed out: " + str((latest.get("status"), latest.get("error"))))

    def test_complete_workflow(self):
        code, _ = self.http("POST", "/api/planning/analyze", {}, token=False)
        self.assertEqual(code, 403); self.mark("missing_token_403")
        code, _ = self.http("POST", "/api/planning/analyze", {}, origin="https://untrusted.example")
        self.assertEqual(code, 403); self.mark("foreign_origin_403")
        code, plan = self.http("POST", "/api/planning/analyze", {"uplink_gbps": 50, "memory_gib": 24, "steps": 30})
        self.assertEqual(code, 200)
        self.assertEqual(plan["mode"], "computed_simulation")
        self.assertEqual(plan["selected_variant"], "compact")
        self.assertTrue(plan["comparison_contract"]["same_workload_hash"])
        rejected = next(c for c in plan["candidates"] if not c["admissible"])
        code, _ = self.http("POST", "/api/planning/apply", {"run_id": plan["run_id"], "candidate_id": rejected["id"]})
        self.assertEqual(code, 400); self.mark("rejected_candidate_400")
        selection = {"run_id": plan["run_id"], "candidate_id": plan["selected_candidate_id"],
                     "uplink_gbps": 5, "memory_gib": 1, "blocked_nodes": ["n0"], "steps": 1}
        code, execution = self.http("POST", "/api/planning/apply", selection)
        self.assertEqual(code, 200)
        result = execution["evaluation"]
        self.assertEqual(result["input_hash"], plan["selected_candidate"]["input_hash"])
        self.assertEqual(result["workload_hash"], plan["workload_hash"])
        self.assertEqual(result["topology"]["uplink_bandwidth_gbps"], 50)
        self.assertEqual(result["spec"]["faults"], [])
        self.assertTrue(all(j["steps"] == 30 for j in result["jobs"]))
        self.assertTrue(result["metrics"]["all_jobs_completed"])
        self.assertTrue(all(c["passed"] for c in result["result"]["invariant_checks"]))
        self.mark("frozen_payload_hash_workload_and_invariants", {"input_hash": result["input_hash"],
            "workload_hash": result["workload_hash"], "makespan_ms": result["metrics"]["makespan_ms"]})
        code, repeated = self.http("POST", "/api/planning/apply", selection)
        self.assertEqual(code, 200); self.assertEqual(repeated["executed_at"], execution["executed_at"])
        self.mark("application_idempotent")
        code, _ = self.http("POST", "/api/planning/apply", {"run_id": plan["run_id"], "candidate_id": "compact_reverse"})
        self.assertEqual(code, 409); self.mark("different_second_apply_409")
        code, blocked = self.http("POST", "/api/planning/analyze", {"memory_gib": 1})
        self.assertEqual(code, 200); self.assertEqual(blocked["status"], "blocked")
        self.assertIsNone(blocked["selected_candidate"])
        self.assertTrue(all(not c["admissible"] for c in blocked["candidates"]))
        self.assertTrue(all(any(x["code"] == "memory" and not x["passed"] for x in c["checks"]) for c in blocked["candidates"]))
        self.mark("memory_blocked", {"candidates_rejected": len(blocked["candidates"])})
        for _ in range(19):
            code, _ = self.http("POST", "/api/planning/analyze", {"memory_gib": 1})
            self.assertEqual(code, 200)
        code, _ = self.http("POST", "/api/planning/apply", selection)
        self.assertEqual(code, 409); self.mark("actually_evicted_plan_409", {"newer_analyses": 20})
        code, gpu = self.http("GET", "/api/gpu-evidence")
        self.assertEqual(code, 200); self.assertEqual(gpu["mode"], "recorded_gpu_evidence")
        self.assertIs(gpu["is_live"], False); self.assertIn("回放", gpu["disclosure"])
        self.assertEqual(len(gpu["stages"]), 7)
        self.mark("gpu_history_labeled", {"is_live": gpu["is_live"], "mode": gpu["mode"], "captured_at": gpu["captured_at"]})
        code, tcp = self.http("POST", "/api/tcp/start")
        self.tcp_started = True
        self.tcp_session_id = tcp.get("session_id")
        self.assertEqual(code, 200); self.assertEqual(tcp["status"], "running", tcp.get("error"))
        self.assertEqual(tcp["mode"], "live_local_tcp"); self.assertEqual(tcp["source"], "MEASURED")
        initial_ack = tcp["metrics"]["acknowledged_blocks"]
        self.assertGreaterEqual(initial_ack, 2)
        growing = self.until_tcp(lambda state: state["metrics"]["acknowledged_blocks"] > initial_ack)
        self.mark("live_tcp_initial_ack_growth", {"session_id": tcp["session_id"], "initial": initial_ack,
                                                 "later": growing["metrics"]["acknowledged_blocks"]})
        code, _ = self.http("POST", "/api/tcp/inject", {"kind": "channel_a", "session_id": self.tcp_session_id})
        self.assertEqual(code, 200)
        recovered = self.until_tcp(lambda state: state["status"] == "recovered")
        self.assertEqual(recovered["metrics"]["current_route"], "B")
        self.assertFalse(recovered["topology"]["primary"]["alive"])
        self.assertTrue(recovered["topology"]["receiver"]["alive"])
        self.assertTrue(recovered["topology"]["spare"]["alive"])
        self.assertEqual(len(recovered["stages"]), 7)
        self.assertTrue(all(s["state"] == "done" for s in recovered["stages"]))
        verification = recovered["stages"][6]["facts"]
        self.assertTrue(verification["recovered"])
        deliveries = verification["details"]["deliveries"]
        self.assertEqual(len(deliveries), 2)
        self.assertTrue(all(d["ok"] and d["channel"] == "B" and d["bytes"] == 65536 for d in deliveries))
        self.assertNotEqual(deliveries[0]["chunk_id"], deliveries[1]["chunk_id"])
        recovery_ack = recovered["metrics"]["acknowledged_blocks"]
        later = self.until_tcp(lambda state: state["metrics"]["acknowledged_blocks"] > recovery_ack)
        self.mark("seven_stages_actual_failure_recovery_and_new_ack", {"session_id": recovered["session_id"],
            "route": "B", "failed_attempts": recovered["metrics"]["failed_attempts"],
            "ack_at_recovery": recovery_ack, "ack_after": later["metrics"]["acknowledged_blocks"],
            "verified_chunk_ids": [d["chunk_id"] for d in deliveries]})
        code, stopped = self.http("POST", "/api/tcp/stop", {"session_id": self.tcp_session_id})
        self.assertEqual(code, 200); self.assertTrue(stopped["cleanup_complete"])
        self.assertEqual(stopped["status"], "stopped")
        for key in ["receiver", "primary", "spare"]:
            self.assertFalse(stopped["topology"][key]["alive"])
        self.tcp_started = False
        self.mark("owned_tcp_children_cleaned", {"processes": {k: stopped["topology"][k] for k in ["receiver", "primary", "spare"]}})
        previous_session_id = self.tcp_session_id
        code, new_session = self.http("POST", "/api/tcp/start")
        self.tcp_started = True
        self.tcp_session_id = new_session.get("session_id")
        self.assertEqual(code, 200)
        self.assertEqual(new_session["status"], "running")
        self.assertNotEqual(self.tcp_session_id, previous_session_id)
        for path, body in [("/api/tcp/inject", {"kind": "channel_a", "session_id": previous_session_id}),
                           ("/api/tcp/stop", {"session_id": previous_session_id})]:
            code, _ = self.http("POST", path, body)
            self.assertEqual(code, 409)
            code, unchanged = self.http("GET", "/api/tcp/status")
            self.assertEqual(code, 200)
            self.assertEqual(unchanged["session_id"], self.tcp_session_id)
            self.assertEqual(unchanged["status"], "running")
            self.assertEqual(unchanged["metrics"]["current_route"], "A")
            self.assertTrue(all(unchanged["topology"][k]["alive"] for k in ["receiver", "primary", "spare"]))
        acknowledged = unchanged["metrics"]["acknowledged_blocks"]
        progressing = self.until_tcp(lambda state: state["metrics"]["acknowledged_blocks"] > acknowledged)
        self.mark("stale_session_inject_and_stop_409_new_session_unchanged", {
            "previous_session_id": previous_session_id, "current_session_id": self.tcp_session_id,
            "current_status": progressing["status"], "ack_before": acknowledged,
            "ack_after": progressing["metrics"]["acknowledged_blocks"]})
        code, stopped = self.http("POST", "/api/tcp/stop", {"session_id": self.tcp_session_id})
        self.assertEqual(code, 200)
        self.assertTrue(stopped["cleanup_complete"])
        self.assertTrue(all(not stopped["topology"][k]["alive"] for k in ["receiver", "primary", "spare"]))
        self.tcp_started = False
        self.mark("second_session_owned_children_cleaned", {"session_id": self.tcp_session_id})
        code, export = self.http("GET", "/api/export")
        self.assertEqual(code, 200); self.assertTrue(export["tcp"]["cleanup_complete"])
        self.assertEqual(export["recorded_gpu"]["mode"], "recorded_gpu_evidence")
        self.mark("export_mode_boundaries")
        self.audit["all_assertions_passed"] = True

    def cleanup_server(self):
        try:
            if self.tcp_started and self.url and self.token:
                try:
                    code, state = self.http("POST", "/api/tcp/stop", {"session_id": self.tcp_session_id})
                    self.audit["cleanup"]["fallback_tcp_stop"] = {"code": code, "complete": state.get("cleanup_complete")}
                except Exception as exc:
                    self.audit["cleanup"]["tcp_error"] = str(exc)
            if self.process:
                if self.process.poll() is None:
                    self.process.terminate()
                try:
                    self.process.wait(timeout=35)
                except subprocess.TimeoutExpired:
                    self.process.kill(); self.process.wait(timeout=5)
                self.audit["cleanup"].update(server_returncode=self.process.returncode, server_exited=self.process.poll() is not None)
                if hasattr(self, "reader"):
                    self.reader.join(timeout=1)
                if self.process.stdout:
                    self.process.stdout.close()
        finally:
            self.audit["finished_at"] = time.time()
            self.audit.setdefault("all_assertions_passed", False)
            (ROOT / "audit").mkdir(exist_ok=True)
            (ROOT / "audit/http_checks.json").write_text(json.dumps(self.audit, ensure_ascii=False, indent=2))
            (ROOT / "audit/http_server.log").write_text("\n".join(self.lines) + "\n")


if __name__ == "__main__":
    unittest.main(verbosity=2)
