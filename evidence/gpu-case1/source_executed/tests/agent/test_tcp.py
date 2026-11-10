from __future__ import annotations

import copy
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from agent_demo.tcp_backend import TCPAdapter
from agent_demo.tcp_worker import Receiver, exchange


class TCPPureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.adapter = TCPAdapter(Path(self.temp.name))
        self.adapter._probe = lambda name: {"ok": name != "A"}

    def candidate(self):
        return self.adapter.candidates({"code": "channel_unavailable"})[1]

    def test_unavailable_and_tampered_candidates_are_rejected(self):
        plans = self.adapter.candidates({"code": "channel_unavailable"})
        self.assertIn("candidate_channel_unreachable", self.adapter.validate(plans[0]))
        changed = copy.deepcopy(plans[1])
        changed["cost"] = -1
        self.assertIn("candidate_not_issued_or_modified", self.adapter.validate(changed))

    def test_idempotency_and_stale_version(self):
        plan = self.candidate()
        first = self.adapter.apply(plan, idempotency_key="one")
        self.assertEqual(first, self.adapter.apply(plan, idempotency_key="one"))
        self.assertEqual(self.adapter.version, 1)
        with self.assertRaisesRegex(ValueError, "stale_snapshot_version"):
            self.adapter.apply(plan, idempotency_key="two")
        changed = copy.deepcopy(plan)
        changed["target"] = "A"
        with self.assertRaisesRegex(ValueError, "different candidate"):
            self.adapter.apply(changed, idempotency_key="one")

    def test_diagnosis_requires_independent_receiver_evidence(self):
        observation = {"evidence": [{"id": "one"}], "version": 0,
                       "facts": {"active_channel": "A", "delivery": {"ok": False},
                                 "probes": {"receiver": {"ok": False}, "A": {"ok": False}}}}
        self.assertEqual(self.adapter.diagnose(observation)["code"], "inconclusive")
        observation["facts"]["probes"]["receiver"]["ok"] = True
        diagnosed = self.adapter.diagnose(observation)
        self.assertEqual(diagnosed["code"], "channel_unavailable")
        self.assertEqual(diagnosed["evidence_ids"], ["one"])

    def test_payload_dedup_and_checksum(self):
        receiver = Receiver(Path(self.temp.name) / "state.json")
        request = self.adapter._next_payload()
        first, duplicate = receiver.request(request), receiver.request(request)
        self.assertTrue(first["ok"])
        self.assertFalse(first["duplicate"])
        self.assertTrue(duplicate["duplicate"])
        self.assertEqual(receiver.stats()["unique_chunks"], 1)
        corrupted = dict(request, sha256="bad")
        self.assertFalse(receiver.request(corrupted)["ok"])
        self.assertEqual(receiver.stats()["unique_bytes"], 65536)
        receiver.request({"op": "ping"})
        self.assertEqual(receiver.stats()["unique_chunks"], 1)

    def test_new_payload_and_pending_retry_with_in_memory_transport(self):
        """Exercise protocol accounting without claiming actual socket execution."""
        receiver = Receiver(Path(self.temp.name) / "receiver.json")
        adapter = TCPAdapter(Path(self.temp.name) / "run")
        adapter._started = True
        adapter.endpoints = {"receiver": 1, "A": 2, "B": 3}
        unavailable = set()

        def transport(port, request, timeout):
            if port in unavailable:
                raise ConnectionRefusedError("test transport unavailable")
            return receiver.request(request)

        with patch("agent_demo.tcp_backend.exchange", side_effect=transport):
            self.assertTrue(adapter.baseline()["recovered"])
            unavailable.add(2)
            observation = adapter.observe()
            failed_id = observation["facts"]["delivery"]["chunk_id"]
            diagnosis = adapter.diagnose(observation)
            plans = adapter.candidates(diagnosis)
            self.assertTrue(adapter.validate(plans[0]))
            self.assertEqual(adapter.validate(plans[1]), [])
            receipt = adapter.apply(plans[1], idempotency_key="switch")
            result = adapter.verify(receipt)
            self.assertTrue(result["recovered"])
            self.assertEqual(result["details"]["deliveries"][0]["chunk_id"], failed_id)
            self.assertNotEqual(result["details"]["deliveries"][1]["chunk_id"], failed_id)
            self.assertEqual(receiver.stats()["unique_chunks"], 4)
            self.assertEqual(adapter.failed_attempts, 1)

    def test_plan_write_failure_does_not_change_running_channel(self):
        plan = self.candidate()
        with patch("agent_demo.tcp_backend.atomic_json", side_effect=OSError("disk unavailable")):
            with self.assertRaises(OSError):
                self.adapter.apply(plan, idempotency_key="write-fails")
        self.assertEqual((self.adapter.active, self.adapter.version), ("A", 0))


class TCPLiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.adapter = TCPAdapter(Path(self.temp.name), timeout=0.5)
        self.addCleanup(self.adapter.close)
        try:
            self.adapter.start()
        except PermissionError as exc:
            self.skipTest(f"Local TCP sockets unavailable in execution sandbox: {exc}")

    def test_actual_failure_replan_retry_and_new_payload(self):
        self.assertTrue(self.adapter.baseline()["recovered"])
        self.adapter.inject("channel_a")
        observed = self.adapter.observe()
        self.assertEqual(observed["status"], "failed")
        self.assertNotIn("ground_truth", str(observed))
        pending_id = observed["facts"]["delivery"]["chunk_id"]
        diagnosis = self.adapter.diagnose(observed)
        self.assertEqual(diagnosis["code"], "channel_unavailable")
        plans = self.adapter.candidates(diagnosis)
        viable = [plan for plan in plans if not self.adapter.validate(plan)]
        self.assertEqual([p["target"] for p in viable], ["B"])
        receipt = self.adapter.apply(viable[0], idempotency_key="real-switch")
        verified = self.adapter.verify(receipt)
        self.assertTrue(verified["recovered"])
        self.assertEqual(verified["details"]["deliveries"][0]["chunk_id"], pending_id)
        self.assertNotEqual(verified["details"]["deliveries"][1]["chunk_id"], pending_id)
        self.assertEqual(verified["details"]["receiver"]["unique_chunks"], 4)

    def test_no_available_channel(self):
        self.adapter.inject("all_channels")
        observation = self.adapter.observe()
        plans = self.adapter.candidates(self.adapter.diagnose(observation))
        self.assertEqual(len(plans), 2)
        self.assertTrue(all(self.adapter.validate(plan) for plan in plans))

    def test_receiver_failure_is_not_misdiagnosed_as_channel_only(self):
        self.adapter.inject("receiver")
        observation = self.adapter.observe()
        self.assertEqual(self.adapter.diagnose(observation)["code"], "inconclusive")

    def test_real_receiver_rejects_corrupt_payload(self):
        request = self.adapter._next_payload()
        request["sha256"] = "bad"
        response = exchange(self.adapter.endpoints["A"], request, 0.5)
        self.assertFalse(response["ok"])


if __name__ == "__main__":
    unittest.main()
