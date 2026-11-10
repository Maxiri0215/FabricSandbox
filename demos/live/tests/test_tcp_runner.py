"""Actual subprocess/socket integration checks, not simulated network results."""
from pathlib import Path
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tcp_runner import TCPDemoSession


class LiveTCPTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="fabric-tcp-test-")
        self.sessions = []

    def tearDown(self):
        for session in self.sessions:
            session.close()
            snap = session.snapshot()
            self.assertTrue(snap["cleanup_complete"])
            for role in ("receiver", "primary", "spare"):
                self.assertFalse(snap["topology"][role]["alive"])
        self.tmp.cleanup()

    def session(self, **kwargs):
        s = TCPDemoSession(Path(self.tmp.name), max_seconds=kwargs.pop("max_seconds", 20),
                           transfer_interval=0.05, **kwargs)
        self.sessions.append(s)
        v = s.start()
        self.assertEqual(v["status"], "running", v.get("error"))
        return s

    def wait_terminal(self, s, timeout=12):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            v = s.snapshot()
            if v["status"] != "recovering":
                return v
            time.sleep(0.02)
        self.fail("Recovery exceeded bounded test timeout")

    def test_real_channel_failover_retry_fresh_progress_and_owned_cleanup(self):
        s = self.session()
        initial = s.snapshot()
        pids = [initial["topology"][k]["pid"] for k in ("receiver", "primary", "spare")]
        ports = [initial["topology"][k]["port"] for k in ("receiver", "primary", "spare")]
        self.assertEqual(len(set(pids)), 3)
        self.assertEqual(len(set(ports)), 3)
        self.assertTrue(all(x > 0 for x in pids))
        s.inject()
        v = self.wait_terminal(s)
        self.assertEqual(v["status"], "recovered")
        self.assertEqual(v["metrics"]["current_route"], "B")
        self.assertFalse(v["topology"]["primary"]["alive"])
        self.assertTrue(v["topology"]["receiver"]["alive"])
        self.assertTrue(v["topology"]["spare"]["alive"])
        self.assertEqual([v["topology"][k]["pid"] for k in ("receiver", "primary", "spare")], pids)
        self.assertEqual(v["incident"]["tool_calls"], 6)
        self.assertEqual([x["state"] for x in v["stages"]], ["done"] * 7)
        verification = v["incident"]["verification"]
        self.assertTrue(verification["recovered"])
        deliveries = verification["details"]["deliveries"]
        failed_id = v["incident"]["observation"]["facts"]["delivery"]["chunk_id"]
        self.assertEqual(deliveries[0]["chunk_id"], failed_id)
        self.assertNotEqual(deliveries[0]["chunk_id"], deliveries[1]["chunk_id"])
        for d in deliveries:
            self.assertTrue(d["ok"])
            self.assertEqual(d["response"]["sha256"], d["sha256"])
            self.assertEqual(d["response"]["bytes"], 65536)
        self.assertTrue(v["metrics"]["failed_attempts"] >= 1)
        before = v["metrics"]["acknowledged_blocks"]
        deadline = time.monotonic() + 2
        while s.snapshot()["metrics"]["acknowledged_blocks"] <= before and time.monotonic() < deadline:
            time.sleep(0.03)
        self.assertGreater(s.snapshot()["metrics"]["acknowledged_blocks"], before)
        with self.assertRaises(RuntimeError):
            s.inject()
        closed = s.close()
        self.assertEqual(closed["status"], "stopped")
        self.assertEqual(closed["terminal_status"], "recovered")

    def test_both_paths_down_blocks_without_apply(self):
        s = self.session()
        s.inject("all_channels")
        v = self.wait_terminal(s)
        self.assertEqual(v["status"], "blocked")
        self.assertFalse(v["incident"]["attempts"])
        self.assertFalse(any(r["feasible"] for r in v["incident"]["candidate_rounds"][-1]))
        self.assertEqual(v["metrics"]["current_route"], "A")
        self.assertEqual(v["metrics"]["version"], 0)

    def test_receiver_failure_is_inconclusive_not_invented_channel_fix(self):
        s = self.session()
        s.inject("receiver")
        v = self.wait_terminal(s)
        self.assertEqual(v["terminal_status"], "inconclusive")
        self.assertEqual(v["status"], "failed")
        self.assertEqual(v["incident"]["diagnosis"]["code"], "inconclusive")
        self.assertFalse(v["incident"]["attempts"])

    def test_time_limit_cleans_up(self):
        s = self.session(max_seconds=5)
        deadline = time.monotonic() + 8
        while not s.snapshot()["cleanup_complete"] and time.monotonic() < deadline:
            time.sleep(0.1)
        self.assertTrue(s.snapshot()["cleanup_complete"])
        self.assertEqual(s.snapshot()["status"], "stopped")

    def test_invalid_injection_rejected_before_any_action(self):
        s = self.session()
        with self.assertRaises(ValueError):
            s.inject("arbitrary_pid")
        self.assertTrue(all(s.snapshot()["topology"][k]["alive"] for k in ("receiver", "primary", "spare")))


if __name__ == "__main__":
    unittest.main()
