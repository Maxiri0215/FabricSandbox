"""Standard-library supervisor for a real, explicitly allocated two-rank DDP job.

No torch import occurs here. The injector's ground truth is never a diagnostic
input. Restarting a worker group is an application recovery, not hardware repair.
"""
from __future__ import annotations

import hashlib
import itertools
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from uuid import uuid4

from .common import append_event, atomic_json, canonical_hash, read_json


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _alive(pid: int) -> bool:
    try:
        proc_stat = Path(f"/proc/{pid}/stat")
        if proc_stat.exists() and proc_stat.read_text().rsplit(")", 1)[1].split()[0] == "Z":
            return False  # A zombie has already released its CUDA resources.
        os.kill(pid, 0)
        return True
    except (OSError, ValueError, IndexError):
        return False


def _process_identity(pid: int) -> dict | None:
    """Read OS birth identity; a PID alone is unsafe after its process exits."""
    try:
        pgid = os.getpgid(pid)
        proc = Path(f"/proc/{pid}/stat")
        if proc.exists():
            fields = proc.read_text().rsplit(")", 1)[1].split()
            if fields[0] == "Z":
                return None
            return {"pid": pid, "pgid": pgid, "start_token": fields[19], "method": "linux_proc_start_ticks"}
        # macOS fallback: start timestamp plus the complete process command. This
        # is explicitly weaker than Linux start ticks, but avoids PID-only kills.
        result = subprocess.run(["ps", "-p", str(pid), "-o", "lstart=", "-o", "command="],
                                capture_output=True, text=True, timeout=2)
        token = result.stdout.strip()
        if result.returncode or not token:
            return None
        return {"pid": pid, "pgid": pgid, "start_token": token, "method": "ps_birth_and_command"}
    except (OSError, IndexError, subprocess.TimeoutExpired):
        return None


class DDPAdapter:
    backend = "live_gpu"

    def __init__(self, output: Path, gpus: list[str], *, device="cuda", model="gpt2",
                 steps=20, checkpoint_every=5, sequence_length=128, timeout=180.0):
        self.output = Path(output).resolve()
        self.output.mkdir(parents=True, exist_ok=True)
        self.gpus = [str(x) for x in gpus]
        if len(self.gpus) < 2 or len(set(self.gpus)) != len(self.gpus):
            raise ValueError("Provide at least two distinct, explicitly authorized execution slots")
        if any(not x or "," in x or any(c.isspace() for c in x) for x in self.gpus):
            raise ValueError("Invalid GPU/slot identifier")
        if device not in {"cuda", "cpu"} or model not in {"gpt2", "tiny"}:
            raise ValueError("Unsupported device/model")
        if not 2 <= steps <= 10000 or not 1 <= checkpoint_every <= steps:
            raise ValueError("Invalid training step/checkpoint configuration")
        if not 2 <= sequence_length <= 1024 or not 1 <= timeout <= 1800:
            raise ValueError("Invalid sequence length/timeout")
        self.device, self.model = device, model
        self.backend = "live_gpu" if device == "cuda" else "live_cpu_ddp"
        self.steps, self.checkpoint_every = steps, checkpoint_every
        self.sequence_length, self.timeout = sequence_length, float(timeout)
        self.version, self.epoch = 0, 0
        self.current = self.gpus[:2]
        self.process: subprocess.Popen | None = None
        self._log_handle = None
        self._receipts: dict[str, tuple[str, dict]] = {}
        self._quarantined: set[str] = set()
        self._observed_exits: dict[int, dict] = {}
        self._last_observation: dict | None = None
        self._gate: int | None = None
        self._owned_pids: set[int] = set()
        self._identities: dict[int, dict] = {}
        self.run_id = uuid4().hex
        self._preflight: dict = {}
        self._worker = Path(__file__).with_name("ddp_worker.py")

    def _arguments(self):
        return ["--output", str(self.output), "--device", self.device, "--model", self.model,
                "--steps", str(self.steps), "--checkpoint-every", str(self.checkpoint_every),
                "--sequence-length", str(self.sequence_length), "--timeout", str(self.timeout)]

    def _environment(self, slots):
        env = os.environ.copy()
        env["FABRIC_GPU_SLOTS"] = json.dumps(slots)
        env["FABRIC_RUN_ID"] = self.run_id
        if self.device == "cuda":
            env["CUDA_VISIBLE_DEVICES"] = ",".join(slots)
        else:
            env["CUDA_VISIBLE_DEVICES"] = ""
        env["OMP_NUM_THREADS"] = "1"
        env["TOKENIZERS_PARALLELISM"] = "false"
        env["HF_HUB_OFFLINE"] = "1"
        env["PYTHONUNBUFFERED"] = "1"
        env["TORCH_NCCL_ASYNC_ERROR_HANDLING"] = "1"
        return env

    def start(self, *, fault_at_step: int | None = None):
        if os.name == "nt":
            raise RuntimeError("Live DDP process-group supervision requires Linux/macOS; TCP demo supports Windows")
        if self.process is not None:
            raise RuntimeError("Adapter has already started")
        if any(self.output.glob("epoch-*")):
            raise ValueError("DDP output contains an earlier run; use a new directory")
        if fault_at_step is not None:
            if not self.checkpoint_every < fault_at_step < self.steps:
                raise ValueError("fault_at_step must be after the first checkpoint and before the final step")
        self._gate = fault_at_step
        command = [sys.executable, str(self._worker), "--preflight", *self._arguments()]
        result = subprocess.run(command, env=self._environment(self.gpus), text=True,
                                capture_output=True, timeout=self.timeout)
        (self.output / "preflight.log").write_text(result.stdout + result.stderr, encoding="utf-8")
        if result.returncode:
            raise RuntimeError("DDP preflight failed; see preflight.log (torch/CUDA/disk/model dependency check)")
        self._preflight = read_json(self.output / "preflight.json", {})
        atomic_json(self.output / "experiment_config.json", {
            "backend": self.backend, "allowed_slots": self.gpus, "world_size": 2,
            "model": self.model, "steps": self.steps, "checkpoint_every": self.checkpoint_every,
            "sequence_length": self.sequence_length, "initial_slots": self.current,
            "fault_gate_step": fault_at_step,
            "boundary": "Explicitly allocated slots only. Worker termination is not GPU hardware failure.",
            "performance_note": "Gate/checkpoint/restart overhead is separate from per-step training duration."})
        self._launch()
        return {"pid": self.process.pid, "slots": list(self.current), "epoch": self.epoch}

    def _launch(self, resume_manifest: Path | None = None):
        command = [sys.executable, "-m", "torch.distributed.run", "--standalone",
                   "--max-restarts=0", "--nproc-per-node=2", str(self._worker),
                   *self._arguments(), "--epoch", str(self.epoch)]
        if self.epoch == 0 and self._gate is not None:
            command += ["--fault-gate-step", str(self._gate)]
        if resume_manifest is not None:
            command += ["--resume-manifest", str(resume_manifest)]
        self._log_handle = (self.output / f"torchrun-epoch-{self.epoch}.log").open("w", encoding="utf-8")
        self.process = subprocess.Popen(command, env=self._environment(self.current),
                                        stdout=self._log_handle, stderr=subprocess.STDOUT,
                                        start_new_session=(os.name != "nt"))
        self._owned_pids.add(self.process.pid)
        append_event(self.output / "executor.jsonl", {"kind": "group_started", "epoch": self.epoch,
                     "pid": self.process.pid, "slots": list(self.current), "version": self.version,
                     "resume_manifest": str(resume_manifest) if resume_manifest else None})

    def _ranks(self):
        records = []
        for rank in range(2):
            item = read_json(self.output / f"epoch-{self.epoch}" / f"rank-{rank}.json")
            if item and item.get("epoch") == self.epoch and item.get("run_id") == self.run_id:
                records.append(item)
                self._owned_pids.add(int(item["pid"]))
                identity = item.get("process_identity")
                if identity and identity.get("pid") == item["pid"]:
                    self._identities[int(item["pid"])] = identity
        return records

    def _is_owned_worker(self, pid: int) -> bool:
        expected = self._identities.get(pid)
        return bool(expected and pid in self._owned_pids and _process_identity(pid) == expected)

    def _signal_worker(self, pid: int, signum: int) -> None:
        if not self._is_owned_worker(pid):
            return
        identity = self._identities[pid]
        try:
            if identity["pgid"] == pid:
                # torchrun may create a new session for EACH worker.
                os.killpg(pid, signum)
            else:
                os.kill(pid, signum)
        except ProcessLookupError:
            pass

    def observe(self):
        now = time.time()
        ranks = self._ranks()
        launcher_exit = self.process.poll() if self.process else None
        evidence = [{"id": f"e{self.epoch}-launcher-{time.time_ns()}", "kind": "launcher_status",
                     "target": f"group-{self.epoch}", "value": {"pid": self.process.pid if self.process else None,
                         "exit_code": launcher_exit, "observed_at": now}}]
        exits = []
        for rank in range(2):
            item = read_json(self.output / f"epoch-{self.epoch}" / f"exit-{rank}.json")
            if item and item.get("run_id") == self.run_id and item.get("epoch") == self.epoch:
                exits.append(item)
                evidence.append({"id": f"e{self.epoch}-exit-{rank}", "kind": "worker_exit_observation",
                                 "target": item["slot_id"], "value": item})
        for record in ranks:
            alive = self._is_owned_worker(int(record["pid"]))
            evidence.append({"id": f"e{self.epoch}-rank-{record['rank']}-{time.time_ns()}",
                             "kind": "worker_progress", "target": record["slot_id"],
                             "value": {**record, "pid_alive": alive, "observed_at": now}})
        complete = (len(ranks) == 2 and all(r.get("status") == "completed" and r.get("step") == self.steps
                                          for r in ranks) and launcher_exit == 0)
        failed = launcher_exit not in (None, 0) or bool(exits) or any(r.get("status") == "failed" for r in ranks)
        if self.process is None:
            status = "not_started"
        elif complete:
            status = "completed"
        elif failed:
            status = "failed"
        else:
            status = "healthy" if len(ranks) == 2 else "starting"
        observation = {"status": status, "evidence": evidence,
                       "facts": {"epoch": self.epoch, "version": self.version, "ranks": ranks,
                                 "worker_exits": sorted(exits, key=lambda x: x["observed_at"]),
                                 "launcher_exit_code": launcher_exit,
                                 "current_slots": list(self.current), "allowed_slots": list(self.gpus)}}
        self._last_observation = observation
        return observation

    def diagnose(self, observation):
        ids = [x["id"] for x in observation["evidence"]]
        if observation["status"] == "completed":
            return {"code": "healthy", "evidence_ids": ids, "affected": [], "explanation": "Training completed"}
        if observation["status"] != "failed":
            return {"code": "inconclusive", "evidence_ids": ids, "affected": [],
                    "explanation": "No confirmed worker-group failure; do not infer a GPU hardware fault"}
        exits = observation["facts"]["worker_exits"]
        if exits:
            # First observed worker event is a recovery hint, not proof of hardware causality.
            suspect = str(exits[0]["slot_id"])
            self._quarantined.add(suspect)
            affected = [suspect]
            detail = f"First worker exit observation on execution slot {suspect}; temporarily isolate that slot by policy"
        else:
            affected = list(self.current)
            self._quarantined.update(affected)
            detail = "Group failure without a reliable first-worker observation; conservatively isolate both slots"
        return {"code": "worker_group_failed", "evidence_ids": ids, "affected": affected,
                "explanation": detail + "; restart the complete fixed-size group from a committed checkpoint",
                "certainty": "process failure observed; hardware cause unproven",
                "recovery_policy": "quarantine execution slots for this experiment only"}

    def _checkpoint(self, *, verify_hash=False):
        manifest = read_json(self.output / "checkpoint_manifest.json")
        if not manifest:
            raise ValueError("no committed checkpoint is available")
        path = (self.output / manifest["file"]).resolve()
        if self.output not in path.parents or not path.is_file():
            raise ValueError("checkpoint file is missing or outside the experiment directory")
        if manifest.get("world_size") != 2 or manifest.get("model") != self.model:
            raise ValueError("checkpoint model/world size does not match")
        if not 0 < manifest.get("step", 0) < self.steps:
            raise ValueError("checkpoint has no remaining training work")
        if verify_hash and _sha256(path) != manifest["sha256"]:
            raise ValueError("checkpoint hash mismatch")
        return manifest

    def candidates(self, diagnosis):
        if diagnosis.get("code") != "worker_group_failed":
            return []
        # Keep infeasible alternatives visible so rejection reasons are inspectable.
        options = [tuple(self.current)] + list(itertools.combinations(self.gpus, 2))
        result = []
        for index, slots in enumerate(dict.fromkeys(options)):
            moved = len(set(slots) - set(self.current))
            result.append({"id": f"ddp-v{self.version}-{index}", "version": self.version,
                           "action": "restart_group", "target": list(slots), "cost": moved,
                           "changes": {"from_slots": list(self.current), "to_slots": list(slots),
                                       "moved_slots": moved, "world_size": 2,
                                       "all_rank_processes_restart": True},
                           "policy": "minimize changed slots after feasibility filtering"})
        return result

    def validate(self, candidate):
        reasons = []
        if candidate.get("version") != self.version:
            reasons.append("stale snapshot version")
        if candidate.get("action") != "restart_group":
            reasons.append("unsupported action")
        target = candidate.get("target", [])
        if (not isinstance(target, list) or len(target) != 2 or
                not all(isinstance(x, str) for x in target) or len(set(target)) != 2):
            reasons.append("fixed world_size=2 requires two distinct slots")
            target = []
        if not set(target).issubset(self.gpus):
            reasons.append("target is outside the explicitly authorized pool")
        if set(target) & self._quarantined:
            reasons.append("target includes an execution slot quarantined by recovery policy")
        try:
            self._checkpoint()
        except (ValueError, KeyError, OSError) as exc:
            reasons.append(str(exc))
        if self._last_observation is None or self._last_observation["status"] != "failed":
            reasons.append("no observed failed group requiring restart")
        return reasons

    def _stop_group(self):
        if self.process is None:
            return
        # The process group was created with start_new_session and is owned here.
        if self.process.poll() is None:
            if os.name != "nt":
                try:
                    os.killpg(self.process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
            else:
                self.process.terminate()
            try:
                self.process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                if os.name != "nt":
                    try:
                        os.killpg(self.process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                else:
                    self.process.kill()
                self.process.wait(timeout=8)
        # torchrun normally reaps workers; handle registered surviving children only.
        registered = [int(record["pid"]) for record in self._ranks()]

        survivors = [pid for pid in registered if self._is_owned_worker(pid)]
        for pid in survivors:
            self._signal_worker(pid, signal.SIGTERM)
        deadline = time.monotonic() + 5
        while any(self._is_owned_worker(pid) for pid in survivors) and time.monotonic() < deadline:
            time.sleep(.05)
        for pid in survivors:
            self._signal_worker(pid, signal.SIGKILL)
        deadline = time.monotonic() + 3
        while any(self._is_owned_worker(pid) for pid in survivors) and time.monotonic() < deadline:
            time.sleep(.05)
        if any(self._is_owned_worker(pid) for pid in survivors):
            raise RuntimeError("Old owned worker has not exited; refusing to launch overlapping GPU groups")
        if self._log_handle:
            self._log_handle.close()
            self._log_handle = None
        append_event(self.output / "executor.jsonl", {"kind": "old_group_stopped", "epoch": self.epoch,
                     "launcher_pid": self.process.pid, "exit_code": self.process.returncode})

    def apply(self, candidate, *, idempotency_key):
        fingerprint = canonical_hash(candidate)
        if idempotency_key in self._receipts:
            old_hash, receipt = self._receipts[idempotency_key]
            if old_hash != fingerprint:
                raise ValueError("idempotency key reused with a different candidate")
            return receipt
        reasons = self.validate(candidate)
        if reasons:
            raise ValueError("; ".join(reasons))
        manifest = self._checkpoint(verify_hash=True)
        # Freeze the exact source manifest before new checkpoints can replace latest.
        frozen = self.output / f"resume-for-epoch-{self.epoch + 1}.json"
        atomic_json(frozen, manifest)
        old_epoch, old_slots = self.epoch, list(self.current)
        old_ranks = self._ranks()
        old_pids = [int(r["pid"]) for r in old_ranks]
        common_step = min((r.get("step", 0) for r in old_ranks), default=0)
        observed_exits = (self._last_observation or {}).get("facts", {}).get("worker_exits", [])
        observed_failure_at = min((x["observed_at"] for x in observed_exits), default=None)
        self._stop_group()
        self.current = list(candidate["target"])
        self.epoch += 1
        self.version += 1
        self._launch(frozen)
        receipt = {"backend": self.backend, "candidate_id": candidate["id"],
                   "idempotency_key": idempotency_key, "version": self.version,
                   "old_epoch": old_epoch, "new_epoch": self.epoch,
                   "from_slots": old_slots, "to_slots": list(self.current), "old_worker_pids": old_pids,
                   "launcher_pid": self.process.pid, "checkpoint": manifest,
                   "started_at": time.time(), "status": "started", "world_size": 2,
                   "last_common_reported_step": common_step,
                   "rollback_reported_steps": max(0, common_step - manifest["step"]),
                   "observed_failure_at": observed_failure_at}
        self._receipts[idempotency_key] = (fingerprint, receipt)
        atomic_json(self.output / f"execution-epoch-{self.epoch}.json", receipt)
        return receipt

    def wait_progress(self, step, *, require_gate=False, cancel_event=None):
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            if cancel_event is not None and cancel_event.is_set():
                raise InterruptedError("Experiment driver was cancelled")
            observation = self.observe()
            ranks = observation["facts"]["ranks"]
            if observation["status"] == "failed":
                raise RuntimeError("DDP failed before requested progress; inspect torchrun log and rank observations")
            if len(ranks) == 2 and all(r["step"] >= step and
                   (not require_gate or r["status"] == "experiment_gate") for r in ranks):
                return observation
            time.sleep(.05)
        raise TimeoutError(f"DDP did not reach step {step} within {self.timeout}s")

    def wait_checkpoint(self):
        observation = self.wait_progress(self.checkpoint_every)
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            try:
                return self._checkpoint(verify_hash=True)
            except ValueError:
                if self.observe()["status"] == "failed":
                    raise RuntimeError("DDP failed before checkpoint commit")
                time.sleep(.05)
        raise TimeoutError("No checkpoint commit observed")

    def inject(self, kind="worker", at_step=7, *, wait_observed=True, cancel_event=None):
        """Experiment driver only. This manifest is never consumed by diagnosis."""
        if kind != "worker":
            raise ValueError("Supported DDP injection: worker termination")
        if self._gate is not None and self._gate != at_step:
            raise ValueError("Injection step does not match the configured experiment gate")
        original_epoch, original_version = self.epoch, self.version
        observation = self.wait_progress(at_step, require_gate=self._gate is not None, cancel_event=cancel_event)
        self._checkpoint(verify_hash=True)
        victim = next(r for r in observation["facts"]["ranks"] if r["rank"] == 1)
        pid = int(victim["pid"])
        if not self._is_owned_worker(pid):
            raise RuntimeError("Registered victim is not a live process owned by this experiment")
        if cancel_event is not None and cancel_event.is_set():
            raise InterruptedError("Experiment driver was cancelled before injection")
        if (self.epoch, self.version) != (original_epoch, original_version):
            raise RuntimeError("Controller already replaced the original group; injection cancelled")
        truth = {"kind": "worker_termination", "pid": pid, "rank": victim["rank"],
                 "slot_id": victim["slot_id"], "step": victim["step"], "injected_at": time.time(),
                 "boundary": "Ground truth for post-run scoring; not a diagnostic input"}
        atomic_json(self.output / "ground_truth.json", truth)
        self._signal_worker(pid, signal.SIGTERM)
        if not wait_observed:
            return truth
        deadline = time.monotonic() + min(30., self.timeout)
        while time.monotonic() < deadline:
            if self.observe()["status"] == "failed":
                return truth
            time.sleep(.02)
        raise TimeoutError("Injected worker termination was not observed")

    def wait_completed(self):
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            observation = self.observe()
            if observation["status"] in {"completed", "failed"}:
                break
            time.sleep(.1)
        else:
            observation = self.observe()
        ranks = observation["facts"]["ranks"]
        checks = {"two_rank_records": len(ranks) == 2,
                  "all_ranks_completed": len(ranks) == 2 and all(r.get("status") == "completed" for r in ranks),
                  "target_steps_reached": len(ranks) == 2 and all(r.get("step") == self.steps for r in ranks),
                  "fixed_world_size": len(ranks) == 2 and all(r.get("world_size") == 2 for r in ranks),
                  "finite_final_loss": len(ranks) == 2 and all(isinstance(r.get("loss"), (int, float)) and
                                                              math.isfinite(r["loss"]) for r in ranks),
                  "launcher_exit_zero": observation["facts"]["launcher_exit_code"] == 0}
        result = {"status": observation["status"], "completed": all(checks.values()),
                  "recovered": all(checks.values()), "checks": checks, "ranks": ranks,
                  "evidence": observation["evidence"], "backend": self.backend,
                  "mapping_verification": self._mapping_verification(ranks, self.current)}
        atomic_json(self.output / f"completion-epoch-{self.epoch}.json", result)
        return result

    def _mapping_verification(self, ranks, expected_slots):
        configured = len(ranks) == 2 and {r["slot_id"] for r in ranks} == set(expected_slots)
        expected_uuid = {item["slot_id"]: item.get("uuid", "unavailable")
                         for item in self._preflight.get("devices", [])}
        uuid_available = self.device == "cuda" and len(ranks) == 2 and all(
            expected_uuid.get(r["slot_id"], "unavailable") != "unavailable" and
            r.get("device_uuid", "unavailable") != "unavailable" for r in ranks)
        matches = uuid_available and all(r["device_uuid"] == expected_uuid[r["slot_id"]] for r in ranks)
        return {"configured_slot_mapping": configured,
                "mapping_verification_level": "uuid_verified" if matches else (
                    "uuid_mismatch" if uuid_available else ("logical_only" if self.device == "cuda" else "cpu_execution_slots")),
                "actual_device_identity_verified": bool(matches),
                "available_uuid_check_passed": bool(matches) if uuid_available else True,
                "note": "Logical-only mapping cannot independently prove physical GPU identity."}

    def verify(self, receipt):
        if receipt.get("new_epoch") != self.epoch or receipt.get("version") != self.version:
            return {"recovered": False, "evidence": [], "details": {"reason": "stale execution receipt"}}
        result = self.wait_completed()
        ranks = result["ranks"]
        source = receipt["checkpoint"]
        mapping = self._mapping_verification(ranks, receipt["to_slots"])
        checks = {**result["checks"],
                  "restored_exact_checkpoint": len(ranks) == 2 and all(
                      (r.get("checkpoint_loaded") or {}).get("sha256") == source["sha256"] and
                      (r.get("checkpoint_loaded") or {}).get("step") == source["step"] for r in ranks),
                  "configured_slot_mapping": mapping["configured_slot_mapping"],
                  "available_uuid_check_passed": mapping["available_uuid_check_passed"],
                  "new_worker_pids": len(ranks) == 2 and all(r["pid"] not in receipt["old_worker_pids"] for r in ranks),
                  "new_effective_progress": len(ranks) == 2 and all(r["step"] > source["step"] for r in ranks)}
        first_progress = []
        for rank in range(2):
            path = self.output / f"epoch-{self.epoch}" / f"rank-{rank}.jsonl"
            if path.exists():
                for line in path.read_text(encoding="utf-8").splitlines():
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if event.get("step", 0) > source["step"] and "train_step_ms" in event:
                        first_progress.append(event)
                        break
        resume_at = max((x["wall_time"] for x in first_progress), default=None) if len(first_progress) == 2 else None
        failure_at = receipt.get("observed_failure_at")
        verified = {"recovered": all(checks.values()), "evidence": result["evidence"],
                    "details": {"checks": checks, "ranks": ranks, "backend": self.backend,
                                "verification_scope": "complete training after checkpoint restore",
                                "checkpoint_step": source["step"], "target_step": self.steps,
                                "rollback_reported_steps": receipt.get("rollback_reported_steps"),
                                "first_new_step_per_rank": first_progress,
                                "observed_failure_to_resumed_progress_ms": (resume_at - failure_at) * 1000
                                    if resume_at is not None and failure_at is not None else None,
                                "mapping_verification": mapping}}
        atomic_json(self.output / f"verification-epoch-{self.epoch}.json", verified)
        return verified

    def close(self):
        self._stop_group()

    def baseline(self):
        return self.wait_completed()
