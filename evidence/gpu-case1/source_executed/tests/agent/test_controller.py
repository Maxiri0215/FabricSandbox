from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import unittest

from agent_demo.controller import Controller, ToolRejected
from agent_demo.reporting import write_report


class FakeAdapter:
    backend = "test_double_not_measured"

    def __init__(self, *, healthy=False, blocked=False, verify_ok=True, forged=False, apply_error=False):
        self.healthy, self.blocked, self.verify_ok = healthy, blocked, verify_ok
        self.forged, self.apply_error = forged, apply_error
        self.applied, self.version = 0, 0

    def observe(self):
        return {"status": "healthy" if self.healthy else "failed", "version": self.version,
                "evidence": [{"id": "probe-1", "kind": "test_probe", "target": "A", "value": not self.healthy}]}

    def diagnose(self, obs):
        return {"code": "healthy" if self.healthy else "unreachable",
                "evidence_ids": ["invented" if self.forged else "probe-1"], "affected": ["A"]}

    def candidates(self, diagnosis):
        return [{"id": "bad", "version": self.version, "target": "A", "action": "switch", "cost": 0, "changes": {}},
                {"id": "good", "version": self.version, "target": "B", "action": "switch", "cost": 1, "changes": {}}]

    def validate(self, candidate):
        if candidate["version"] != self.version:
            return ["stale"]
        return ["unreachable"] if self.blocked or candidate["target"] == "A" else []

    def apply(self, candidate, *, idempotency_key):
        self.applied += 1
        if self.apply_error:
            raise RuntimeError("backend execution failed")
        self.version += 1
        self.healthy = True
        return {"version": self.version, "key": idempotency_key}

    def verify(self, receipt):
        return {"recovered": self.verify_ok, "evidence": [{"id": "new-work", "value": self.verify_ok}], "details": {}}


class ScriptPolicy:
    def __init__(self, calls):
        self.calls, self.index = calls, 0

    def next(self, messages, tools):
        name, args = self.calls[min(self.index, len(self.calls)-1)]
        self.index += 1
        return {"role": "assistant", "content": None,
                "tool_calls": [{"id": f"call-{self.index}", "type": "function",
                                "function": {"name": name, "arguments": json.dumps(args)}}]}


GOOD_CALLS = [("observe", {}), ("diagnose", {}), ("propose", {}),
              ("validate", {"candidate_id": "good"}), ("apply", {"candidate_id": "good"}), ("verify", {})]


class ControllerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)

    def controller(self, adapter, **kwargs):
        return Controller(adapter, self.path, verbose=False, **kwargs)

    def test_rule_recovery_retains_rejected_candidate_and_new_evidence(self):
        adapter = FakeAdapter()
        result = self.controller(adapter).run()
        self.assertEqual(result["status"], "recovered")
        self.assertEqual(adapter.applied, 1)
        self.assertFalse(result["candidate_rounds"][0][0]["feasible"])
        self.assertEqual(result["verification"]["evidence"][0]["id"], "new-work")
        self.assertTrue((self.path / "trace.jsonl").is_file())

    def test_resource_shortage_never_executes(self):
        adapter = FakeAdapter(blocked=True)
        result = self.controller(adapter).run()
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(adapter.applied, 0)

    def test_no_fault_no_mutation(self):
        adapter = FakeAdapter(healthy=True)
        self.assertEqual(self.controller(adapter).run()["status"], "healthy")
        self.assertEqual(adapter.applied, 0)

    def test_forged_evidence_is_not_a_diagnosis(self):
        adapter = FakeAdapter(forged=True)
        self.assertEqual(self.controller(adapter).run()["status"], "failed")
        self.assertEqual(adapter.applied, 0)

    def test_failed_verification_cannot_be_overridden_by_healthy_observation(self):
        result = self.controller(FakeAdapter(verify_ok=False)).run()
        self.assertEqual(result["status"], "unresolved")
        self.assertFalse(result["verification"]["recovered"])

    def test_apply_requires_issued_validated_candidate(self):
        controller = self.controller(FakeAdapter())
        controller.call("observe")
        controller.call("diagnose")
        controller.call("propose")
        with self.assertRaises(ToolRejected):
            controller.call("apply", {"candidate_id": "good"})
        with self.assertRaises(ToolRejected):
            controller.call("validate", {"candidate_id": "invented"})

    def test_live_revalidation_rejects_version_change(self):
        adapter = FakeAdapter()
        controller = self.controller(adapter)
        for name, args in GOOD_CALLS[:4]:
            controller.call(name, args)
        adapter.version += 1
        with self.assertRaises(ToolRejected):
            controller.call("apply", {"candidate_id": "good"})
        self.assertEqual(adapter.applied, 0)

    def test_pending_execution_only_allows_verification(self):
        controller = self.controller(FakeAdapter())
        for name, args in GOOD_CALLS[:5]:
            controller.call(name, args)
        for name in ("observe", "diagnose", "propose"):
            with self.assertRaises(ToolRejected):
                controller.call(name)
        self.assertTrue(controller.call("verify")["recovered"])

    def test_llm_calls_real_whitelisted_adapter_tools(self):
        adapter = FakeAdapter()
        policy = ScriptPolicy(GOOD_CALLS)
        result = self.controller(adapter, mode="llm", policy=policy).run()
        self.assertEqual(result["status"], "recovered")
        self.assertEqual(result["effective_mode"], "llm")
        self.assertEqual(policy.index, 6)

    def test_malformed_tool_arguments_are_bounded(self):
        policy = ScriptPolicy([("observe", [])])
        result = self.controller(FakeAdapter(), mode="llm", policy=policy, max_tool_calls=4).run()
        self.assertEqual(result["status"], "unresolved")
        self.assertEqual(policy.index, 4)

    def test_provider_failure_falls_back_but_backend_failure_does_not(self):
        class Offline:
            def next(self, *_):
                raise TimeoutError("not an actual API request")
        result = self.controller(FakeAdapter(), mode="llm", policy=Offline()).run()
        self.assertEqual(result["effective_mode"], "rules_fallback")
        self.assertEqual(result["status"], "recovered")
        adapter = FakeAdapter(apply_error=True)
        result = self.controller(adapter, mode="llm", policy=ScriptPolicy(GOOD_CALLS)).run()
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["effective_mode"], "llm")
        self.assertEqual(adapter.applied, 1)

    def test_html_cannot_close_script_from_untrusted_evidence(self):
        result = {"checks": {"valid": True}, "diagnosis": {"text": "</script><img onerror=alert(1)>"}}
        path = write_report(result, self.path / "report.html")
        content = path.read_text()
        self.assertNotIn("</script><img", content)
        self.assertIn("\\u003c/script>", content)
        self.assertIn("Array.isArray(rawChecks)", content)

    def test_monitor_observes_health_before_independent_anomaly(self):
        class Changing(FakeAdapter):
            reads = 0
            def observe(self):
                if not self.applied:
                    self.healthy = self.reads < 2
                self.reads += 1
                return super().observe()
        result = self.controller(Changing()).run(watch_timeout=2)
        self.assertEqual(result["status"], "recovered")
        states = [e["observation"]["status"] for e in result["events"] if e["kind"] == "monitor_observation"]
        self.assertEqual(states, ["healthy", "failed"])
        self.assertIn("detected_wall_time", result)

    def test_monitor_timeout_does_not_invent_a_failure_or_repair(self):
        adapter = FakeAdapter(healthy=True)
        result = self.controller(adapter).run(watch_timeout=.01)
        self.assertEqual(result["status"], "unresolved")
        self.assertEqual(adapter.applied, 0)


if __name__ == "__main__":
    unittest.main()
