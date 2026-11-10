"""Additional controller negative cases; test doubles are not live evidence."""
from pathlib import Path
import tempfile
import unittest

from agent_demo.controller import Controller
from tests.agent.test_controller import FakeAdapter


class BudgetAndEvidenceTests(unittest.TestCase):
    def test_persistent_failure_exhausts_two_attempts_without_third_apply(self):
        class PersistentFailure(FakeAdapter):
            def observe(self):
                self.healthy = False
                return super().observe()

        with tempfile.TemporaryDirectory() as folder:
            adapter = PersistentFailure(verify_ok=False)
            result = Controller(adapter, Path(folder), max_attempts=2, verbose=False).run()
            self.assertEqual(result['status'], 'unresolved')
            self.assertEqual(adapter.applied, 2)
            self.assertEqual(len(result['attempts']), 2)
            self.assertEqual(result['tool_calls'], 12)
            self.assertTrue(all(not x['verification']['recovered'] for x in result['attempts']))

    def test_success_claim_without_post_execution_evidence_is_not_recovery(self):
        class EmptyEvidence(FakeAdapter):
            def verify(self, receipt):
                return {'recovered': True, 'evidence': [], 'details': {}}

        with tempfile.TemporaryDirectory() as folder:
            adapter = EmptyEvidence()
            result = Controller(adapter, Path(folder), verbose=False).run()
            self.assertEqual(result['status'], 'unresolved')
            self.assertEqual(adapter.applied, 1)
            self.assertEqual(result['verification']['evidence'], [])


if __name__ == '__main__':
    unittest.main()
