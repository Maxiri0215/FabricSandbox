"""Live TCP observation, constrained channel replanning and verified execution.

This is an application-level network testbed, not a physical-switch, RDMA or
GPU-collective emulator. The diagnostic methods never read ground_truth.json.
"""
from __future__ import annotations

import base64
import copy
import hashlib
from pathlib import Path
import subprocess
import sys
import threading
import time

from .common import append_event, atomic_json, canonical_hash, read_json
from .tcp_worker import exchange


class TCPAdapter:
    backend = "live_tcp"

    def __init__(self, output: Path, timeout: float = 2.0, initial_channel: str = "A"):
        if not 0.05 <= timeout <= 30:
            raise ValueError("TCP timeout must be between 0.05 and 30 seconds")
        if initial_channel not in {"A", "B"}:
            raise ValueError("initial_channel must be A or B")
        self.output = Path(output).resolve()
        self.output.mkdir(parents=True, exist_ok=True)
        self.timeout = timeout
        self.version = 0
        self.active = initial_channel
        self.processes: dict[str, subprocess.Popen] = {}
        self.endpoints: dict[str, int] = {}
        self._logs = []
        self._started = False
        self._sequence = 0
        self._evidence_sequence = 0
        self._pending: dict | None = None
        self._issued: dict[str, str] = {}
        self._receipts: dict[str, tuple[str, dict]] = {}
        self._lock = threading.RLock()
        self._start_time = time.monotonic()
        self.attempts = 0
        self.failed_attempts = 0
        self.acknowledged_ids: set[str] = set()

    def _event(self, kind: str, **values) -> None:
        append_event(self.output / "tcp_events.jsonl", {"kind": kind, "backend": self.backend,
                                                       "version": self.version, **values})

    def _evidence(self, kind: str, target: str, value: object) -> dict:
        self._evidence_sequence += 1
        evidence = {"id": f"tcp-e{self._evidence_sequence}", "kind": kind,
                    "target": target, "value": value, "wall_time": time.time()}
        self._event("observation", evidence=evidence)
        return evidence

    def _spawn(self, name: str, mode: str, *options: str) -> int:
        ready_path = self.output / f"{name}_ready.json"
        if ready_path.exists():
            raise ValueError("TCP output contains a previous process run; choose a new output directory")
        log = (self.output / f"{name}_process.log").open("w", encoding="utf-8")
        self._logs.append(log)
        command = [sys.executable, str(Path(__file__).with_name("tcp_worker.py")), mode,
                   "--ready", str(ready_path), "--timeout", str(self.timeout), *options]
        process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT)
        self.processes[name] = process
        deadline = time.monotonic() + 10
        ready = None
        while time.monotonic() < deadline:
            ready = read_json(ready_path)
            if ready is not None:
                break
            if process.poll() is not None:
                raise RuntimeError(f"TCP worker {name} exited during startup; inspect {name}_process.log")
            time.sleep(0.025)
        if not ready:
            raise TimeoutError(f"TCP worker {name} did not publish a listening endpoint")
        if ready.get("ready") is not True:
            if ready.get("errno") in {1, 13}:
                raise PermissionError(ready.get("error", "Local TCP binding denied"))
            raise RuntimeError(ready.get("error", "TCP startup failed"))
        return int(ready["port"])

    def start(self) -> dict:
        if self._started or self.processes:
            raise RuntimeError("TCPAdapter can be started only once")
        try:
            self.endpoints["receiver"] = self._spawn("receiver", "receiver", "--state",
                                                       str(self.output / "receiver_state.json"))
            for channel in ("A", "B"):
                self.endpoints[channel] = self._spawn(channel, "proxy", "--target-port",
                                                       str(self.endpoints["receiver"]))
            self._started = True
            snapshot = {"backend": self.backend, "version": self.version, "active_channel": self.active,
                        "endpoints": dict(self.endpoints), "host": "127.0.0.1",
                        "processes": {k: p.pid for k, p in self.processes.items()},
                        "constraints": {"allowed_channels": ["A", "B"], "payload_bytes": 65536},
                        "channel_paths": {"A": ["client", "channel-A", "receiver"],
                                          "B": ["client", "channel-B", "receiver"]},
                        "boundary": "Application TCP forwarding channels; not physical GPU fabric links"}
            atomic_json(self.output / "tcp_snapshot.json", snapshot)
            return snapshot
        except BaseException:
            self.close()
            raise

    def _probe(self, target: str) -> dict:
        start = time.monotonic()
        try:
            response = exchange(self.endpoints[target], {"op": "ping"}, self.timeout)
            return {"ok": response.get("ok") is True and response.get("probe") is True,
                    "latency_ms": (time.monotonic() - start) * 1000,
                    "response": response}
        except (OSError, ValueError) as exc:
            return {"ok": False, "latency_ms": (time.monotonic() - start) * 1000,
                    "error": type(exc).__name__ + ": " + str(exc)}

    def _next_payload(self) -> dict:
        self._sequence += 1
        chunk_id = f"chunk-{self._sequence:06d}"
        unit = hashlib.sha256(chunk_id.encode()).digest()
        payload = unit * (65536 // len(unit))
        return {"op": "put", "chunk_id": chunk_id,
                "sha256": hashlib.sha256(payload).hexdigest(),
                "payload": base64.b64encode(payload).decode("ascii")}

    def _transfer(self) -> dict:
        if self._pending is None:
            self._pending = self._next_payload()
        request = self._pending
        self.attempts += 1
        start = time.monotonic()
        try:
            response = exchange(self.endpoints[self.active], request, self.timeout)
            correct = (response.get("ok") is True and
                       response.get("chunk_id") == request["chunk_id"] and
                       response.get("sha256") == request["sha256"] and response.get("bytes") == 65536)
            result = {"ok": correct, "channel": self.active, "chunk_id": request["chunk_id"],
                      "sha256": request["sha256"], "bytes": 65536 if correct else 0,
                      "duplicate": response.get("duplicate", False), "response": response}
            if correct:
                self.acknowledged_ids.add(request["chunk_id"])
                self._pending = None
        except (OSError, ValueError) as exc:
            result = {"ok": False, "channel": self.active, "chunk_id": request["chunk_id"],
                      "bytes": 0, "error": type(exc).__name__ + ": " + str(exc)}
        result["elapsed_ms"] = (time.monotonic() - start) * 1000
        if not result["ok"]:
            self.failed_attempts += 1
        self._event("payload_attempt", **result)
        return result

    def observe(self) -> dict:
        if not self._started:
            raise RuntimeError("Call start() before observing TCP")
        probes = {name: self._probe(name) for name in ("receiver", "A", "B")}
        evidence = [self._evidence("tcp_probe", name, value) for name, value in probes.items()]
        transfer = self._transfer()
        evidence.append(self._evidence("payload_delivery", self.active, transfer))
        return {"backend": self.backend, "status": "healthy" if transfer["ok"] else "failed",
                "version": self.version, "evidence": evidence,
                "facts": {"active_channel": self.active, "probes": probes, "delivery": transfer}}

    def diagnose(self, observation: dict) -> dict:
        facts = observation.get("facts", {})
        probes = facts.get("probes", {})
        active = facts.get("active_channel")
        evidence = [x["id"] for x in observation.get("evidence", [])]
        unavailable = [name for name in ("A", "B") if not probes.get(name, {}).get("ok")]
        if facts.get("delivery", {}).get("ok"):
            code, affected, explanation = "healthy", [], "Current channel delivered a checksum-verified payload."
        elif not probes.get("receiver", {}).get("ok"):
            code, affected, explanation = ("inconclusive", ["receiver"],
                "Receiver probe also failed; observations do not isolate a forwarding-channel fault.")
        elif active in unavailable:
            code, affected, explanation = ("channel_unavailable", unavailable,
                "Receiver is reachable, but the active channel fails an end-to-end probe and payload delivery.")
        else:
            code, affected, explanation = ("inconclusive", [str(active)],
                "Payload failed while probes succeeded; gather more evidence instead of inventing a cause.")
        return {"code": code, "evidence_ids": evidence, "affected": affected,
                "explanation": explanation, "current_version": observation.get("version"),
                "certainty": "observed" if code != "inconclusive" else "insufficient"}

    def candidates(self, diagnosis: dict) -> list[dict]:
        if diagnosis.get("code") not in {"channel_unavailable", "healthy"}:
            return []
        candidates = []
        for channel in ("A", "B"):
            candidate = {"id": f"tcp-v{self.version}-use-{channel}", "version": self.version,
                         "action": "set_channel", "target": channel,
                         "cost": 0.0 if channel == self.active else 1.0,
                         "changes": {"channel": {"from": self.active, "to": channel}},
                         "constraints": {"receiver_reachable": True, "channel_reachable": True},
                         "path": ["client", f"channel-{channel}", "receiver"],
                         "selection_rule": "Enumerate both configured paths; reject failed end-to-end probes, then minimize channel changes."}
            self._issued[candidate["id"]] = canonical_hash(candidate)
            candidates.append(candidate)
        return candidates

    def validate(self, candidate: dict) -> list[str]:
        reasons = []
        if self._issued.get(candidate.get("id")) != canonical_hash(candidate):
            reasons.append("candidate_not_issued_or_modified")
        if candidate.get("version") != self.version:
            reasons.append("stale_snapshot_version")
        if candidate.get("action") != "set_channel" or candidate.get("target") not in {"A", "B"}:
            reasons.append("action_or_target_not_allowed")
            return reasons
        if not self._probe("receiver").get("ok"):
            reasons.append("receiver_unreachable")
        if not self._probe(candidate["target"]).get("ok"):
            reasons.append("candidate_channel_unreachable")
        return reasons

    def apply(self, candidate: dict, *, idempotency_key: str) -> dict:
        fingerprint = canonical_hash(candidate)
        if not isinstance(idempotency_key, str) or not idempotency_key:
            raise ValueError("idempotency_key must be a nonempty string")
        with self._lock:
            if idempotency_key in self._receipts:
                previous_hash, receipt = self._receipts[idempotency_key]
                if previous_hash != fingerprint:
                    raise ValueError("idempotency_key reused for a different candidate")
                return copy.deepcopy(receipt)
            reasons = self.validate(candidate)
            if reasons:
                raise ValueError("Plan rejected: " + ", ".join(reasons))
            old = self.active
            receipt = {"backend": self.backend, "candidate_id": candidate["id"],
                       "old_channel": old, "channel": candidate["target"], "version": self.version + 1,
                       "idempotency_key": idempotency_key, "applied_at": time.time()}
            atomic_json(self.output / "active_plan.json", receipt)
            self.active = candidate["target"]
            self.version += 1
            self._receipts[idempotency_key] = (fingerprint, receipt)
            self._event("plan_applied", receipt=receipt)
            return copy.deepcopy(receipt)

    def verify(self, receipt: dict) -> dict:
        if receipt.get("version", self.version) != self.version or receipt.get("channel", self.active) != self.active:
            return {"recovered": False, "evidence": [], "details": {"reason": "receipt_no_longer_current"}}
        first = self._transfer()  # Retry a failed/uncertain block with the same ID.
        second = self._transfer() if first["ok"] else None  # Require fresh successful work too.
        deliveries = [first] + ([second] if second else [])
        evidence = [self._evidence("verified_payload", self.active, item) for item in deliveries]
        try:
            stats = exchange(self.endpoints["receiver"], {"op": "stats"}, self.timeout)
        except (OSError, ValueError) as exc:
            stats = {"ok": False, "error": str(exc)}
        evidence.append(self._evidence("receiver_counters", "receiver", stats))
        recovered = first["ok"] and second is not None and second["ok"] and stats.get("ok") is True
        details = {"channel": self.active, "version": self.version, "deliveries": deliveries,
                   "receiver": stats, "attempts": self.attempts, "failed_attempts": self.failed_attempts,
                   "acknowledged_unique_chunks": len(self.acknowledged_ids),
                   "acknowledged_unique_bytes": len(self.acknowledged_ids) * 65536,
                   "observation_window_seconds": time.monotonic() - self._start_time,
                   "measurement_boundary": "Payload counters exclude ping probes; loopback application transfers are not GPU fabric throughput."}
        result = {"recovered": bool(recovered), "evidence": evidence, "details": details}
        atomic_json(self.output / "tcp_verification.json", result)
        return result

    def baseline(self) -> dict:
        return self.verify({"channel": self.active, "version": self.version})

    def inject(self, kind: str = "channel_a") -> dict:
        targets = {"channel_a": ("A",), "channel_b": ("B",),
                   "all_channels": ("A", "B"), "receiver": ("receiver",)}
        if kind not in targets:
            raise ValueError("Unknown TCP injection kind")
        truth = {"kind": kind, "targets": list(targets[kind]), "injected_at": time.time(),
                 "boundary": "Registered experiment subprocesses only; not diagnostic input"}
        for target in targets[kind]:
            self._stop(target)
        atomic_json(self.output / "ground_truth.json", truth)
        return truth

    def _stop(self, name: str) -> None:
        process = self.processes.get(name)
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)

    def close(self) -> None:
        for name in reversed(tuple(self.processes)):
            self._stop(name)
        for log in self._logs:
            log.close()
        self._logs.clear()
        self._started = False

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *_):
        self.close()
