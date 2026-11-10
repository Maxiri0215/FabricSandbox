from __future__ import annotations

import hashlib
import importlib.util
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from agent_demo.common import atomic_json
from agent_demo.ddp_backend import DDPAdapter


class DDPControlTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.adapter = DDPAdapter(self.root, ["cpu0", "cpu1", "cpu2"], device="cpu", model="tiny")
        payload = b"owned test checkpoint fixture (not torch serialized)"
        (self.root / "checkpoint.pt").write_bytes(payload)
        self.manifest = {"file": "checkpoint.pt", "sha256": hashlib.sha256(payload).hexdigest(),
                         "step": 5, "world_size": 2, "model": "tiny", "sequence_length": 128}
        atomic_json(self.root / "checkpoint_manifest.json", self.manifest)
        self.observation = {"status": "failed", "evidence": [{"id": "observed-worker-exit"}],
                            "facts": {"worker_exits": [{"slot_id": "cpu1", "observed_at": 10}]}}
        self.adapter._last_observation = self.observation

    def select(self):
        diagnosis = self.adapter.diagnose(self.observation)
        options = self.adapter.candidates(diagnosis)
        return next(x for x in options if not self.adapter.validate(x))

    def test_fixed_group_uses_healthy_slot_and_spare(self):
        candidate = self.select()
        self.assertEqual(candidate["target"], ["cpu0", "cpu2"])
        self.assertEqual(candidate["changes"]["world_size"], 2)
        self.assertTrue(candidate["changes"]["all_rank_processes_restart"])

    def test_no_spare_is_blocked_instead_of_reducing_world_size(self):
        self.adapter.gpus = ["cpu0", "cpu1"]
        options = self.adapter.candidates(self.adapter.diagnose(self.observation))
        self.assertTrue(options)
        self.assertTrue(all(self.adapter.validate(x) for x in options))

    def test_ground_truth_is_not_used_as_diagnostic_evidence(self):
        atomic_json(self.root / "ground_truth.json", {"slot_id": "cpu0", "fake_root_cause": "GPU hardware fault"})
        diagnosis = self.adapter.diagnose(self.observation)
        self.assertEqual(diagnosis["affected"], ["cpu1"])
        self.assertEqual(diagnosis["evidence_ids"], ["observed-worker-exit"])
        self.assertIn("hardware cause unproven", diagnosis["certainty"])

    def test_ambiguous_group_failure_is_conservative(self):
        self.observation["facts"]["worker_exits"] = []
        diagnosis = self.adapter.diagnose(self.observation)
        self.assertEqual(set(diagnosis["affected"]), {"cpu0", "cpu1"})
        self.assertTrue(all(self.adapter.validate(x) for x in self.adapter.candidates(diagnosis)))

    def test_stale_or_unauthorized_plan_cannot_execute(self):
        candidate = self.select()
        candidate["version"] = 999
        with self.assertRaisesRegex(ValueError, "stale"):
            self.adapter.apply(candidate, idempotency_key="stale")
        candidate["version"] = 0
        candidate["target"] = ["cpu0", "someone-elses-gpu"]
        with self.assertRaisesRegex(ValueError, "authorized"):
            self.adapter.apply(candidate, idempotency_key="outside")

    def test_hash_mismatch_prevents_restart(self):
        candidate = self.select()
        (self.root / "checkpoint.pt").write_bytes(b"corrupt or half-written checkpoint")
        with patch.object(self.adapter, "_launch") as launch:
            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                self.adapter.apply(candidate, idempotency_key="corrupt")
            launch.assert_not_called()

    def test_duplicate_execution_returns_receipt_without_second_launch(self):
        candidate = self.select()
        self.adapter.process = SimpleNamespace(pid=12345)
        with patch.object(self.adapter, "_stop_group"), patch.object(self.adapter, "_launch") as launch:
            first = self.adapter.apply(candidate, idempotency_key="exactly-once-within-process")
            second = self.adapter.apply(candidate, idempotency_key="exactly-once-within-process")
            self.assertEqual(first, second)
            self.assertEqual(launch.call_count, 1)
            changed = {**candidate, "target": ["cpu2", "cpu0"]}
            with self.assertRaisesRegex(ValueError, "different candidate"):
                self.adapter.apply(changed, idempotency_key="exactly-once-within-process")

    def test_completion_without_checkpoint_restore_is_not_recovery(self):
        self.adapter.version = self.adapter.epoch = 1
        ranks = [{"pid": 80 + i, "slot_id": slot, "step": 20, "checkpoint_loaded": None}
                 for i, slot in enumerate(["cpu0", "cpu2"])]
        receipt = {"new_epoch": 1, "version": 1, "checkpoint": self.manifest,
                   "to_slots": ["cpu0", "cpu2"], "old_worker_pids": [20, 21]}
        with patch.object(self.adapter, "wait_completed", return_value={
                "checks": {"completed": True}, "ranks": ranks, "evidence": []}):
            verified = self.adapter.verify(receipt)
        self.assertFalse(verified["recovered"])
        self.assertFalse(verified["details"]["checks"]["restored_exact_checkpoint"])

    def test_checkpoint_outside_output_is_rejected(self):
        candidate = self.select()
        atomic_json(self.root / "checkpoint_manifest.json", {**self.manifest, "file": "../foreign.pt"})
        self.assertTrue(any("outside" in x for x in self.adapter.validate(candidate)))

    def test_recycled_pid_is_not_owned(self):
        identity = {"pid": 12345, "pgid": 12345, "start_token": "old", "method": "test"}
        self.adapter._owned_pids.add(12345)
        self.adapter._identities[12345] = identity
        with patch("agent_demo.ddp_backend._process_identity", return_value={**identity, "start_token": "new"}):
            self.assertFalse(self.adapter._is_owned_worker(12345))
            with patch("agent_demo.ddp_backend.os.killpg") as kill:
                self.adapter._signal_worker(12345, 15)
                kill.assert_not_called()

    @unittest.skipIf(os.name == "nt", "POSIX process-group test")
    def test_cleanup_handles_worker_in_a_separate_session(self):
        launcher = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], start_new_session=True)
        worker = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], start_new_session=True)
        self.adapter.process = launcher
        identity = {"pid": worker.pid, "pgid": os.getpgid(worker.pid),
                    "start_token": "controlled-test-child", "method": "test"}
        atomic_json(self.root / "epoch-0" / "rank-0.json", {
            "pid": worker.pid, "rank": 0, "slot_id": "cpu0", "epoch": 0,
            "run_id": self.adapter.run_id, "process_identity": identity})
        try:
            self.assertNotEqual(os.getpgid(worker.pid), launcher.pid)
            # Birth-token lookup is separately tested. This sandbox prohibits ps;
            # these are real subprocesses, with identity provided by the test owner.
            with patch("agent_demo.ddp_backend._process_identity",
                       side_effect=lambda pid: identity if pid == worker.pid and worker.poll() is None else None):
                self.adapter.close()
            self.assertIsNotNone(launcher.poll())
            self.assertIsNotNone(worker.poll())
        finally:
            for process in (launcher, worker):
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=5)

    def test_uuid_mismatch_is_not_accepted_as_physical_mapping(self):
        self.adapter.device = "cuda"
        self.adapter._preflight = {"devices": [{"slot_id": "cpu0", "uuid": "GPU-A"},
                                               {"slot_id": "cpu2", "uuid": "GPU-B"}]}
        ranks = [{"slot_id": "cpu0", "device_uuid": "GPU-B"},
                 {"slot_id": "cpu2", "device_uuid": "GPU-A"}]
        checked = self.adapter._mapping_verification(ranks, ["cpu0", "cpu2"])
        self.assertEqual(checked["mapping_verification_level"], "uuid_mismatch")
        self.assertFalse(checked["available_uuid_check_passed"])

    def test_old_torch_without_uuid_is_explicitly_logical_only(self):
        self.adapter.device = "cuda"
        ranks = [{"slot_id": "cpu0"}, {"slot_id": "cpu2"}]
        checked = self.adapter._mapping_verification(ranks, ["cpu0", "cpu2"])
        self.assertEqual(checked["mapping_verification_level"], "logical_only")
        self.assertFalse(checked["actual_device_identity_verified"])


@unittest.skipUnless(importlib.util.find_spec("torch") is not None, "PyTorch is not installed; live CPU DDP not run")
class LiveCPUDDPTests(unittest.TestCase):
    def test_worker_fault_recovers_from_committed_checkpoint(self):
        try:
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", 0))
        except PermissionError:
            self.skipTest("Sandbox forbids loopback binding; real Gloo rendezvous not run")
        with tempfile.TemporaryDirectory() as folder:
            adapter = DDPAdapter(Path(folder), ["cpu0", "cpu1", "cpu2"], device="cpu", model="tiny",
                                 steps=10, checkpoint_every=3, sequence_length=32, timeout=120)
            try:
                adapter.start(fault_at_step=4)
                adapter.inject(at_step=4)
                diagnosis = adapter.diagnose(adapter.observe())
                candidates = adapter.candidates(diagnosis)
                candidate = next(x for x in candidates if not adapter.validate(x))
                receipt = adapter.apply(candidate, idempotency_key="live-test")
                result = adapter.verify(receipt)
                self.assertTrue(result["recovered"], result)
                # Later commits must not delete the recovery source or leave
                # archived checkpoint manifests referring to missing evidence.
                manifests = list((Path(folder) / "checkpoints").glob("manifest-*.json"))
                self.assertGreater(len(manifests), 2)
                from agent_demo.common import read_json
                for path in manifests:
                    manifest = read_json(path)
                    payload = Path(folder) / manifest["file"]
                    self.assertTrue(payload.is_file(), str(payload))
                    self.assertEqual(hashlib.sha256(payload.read_bytes()).hexdigest(), manifest["sha256"])
            finally:
                adapter.close()


if __name__ == "__main__":
    unittest.main()
