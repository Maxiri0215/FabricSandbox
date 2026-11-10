from __future__ import annotations

import unittest

from scripts.run_tests import filter_suite


class TestRunnerSelectionTests(unittest.TestCase):
    def suite(self):
        tests = []
        for test_id in (
            "tests.agent.test_tcp.TCPLiveTests.test_actual_failure",
            "tests.agent.test_ddp.LiveCPUDDPTests.test_restart",
            "tests.agent.test_ddp.DDPControlTests.test_checkpoint",
            "unittest.loader._FailedTest.some_broken_import",
        ):
            test = unittest.FunctionTestCase(lambda: None)
            test.id = lambda value=test_id: value
            tests.append(test)
        return unittest.TestSuite([unittest.TestSuite(tests[:2]), *tests[2:]])

    def test_default_excludes_only_known_live_classes_and_keeps_import_failures(self):
        selected, excluded = filter_suite(self.suite())
        self.assertEqual(selected.countTestCases(), 2)
        self.assertEqual(len(excluded), 2)
        self.assertTrue(all(reason for _, reason in excluded))
        self.assertEqual(
            [test.id() for test in selected],
            ["tests.agent.test_ddp.DDPControlTests.test_checkpoint",
             "unittest.loader._FailedTest.some_broken_import"],
        )

    def test_explicit_live_includes_every_test(self):
        selected, excluded = filter_suite(self.suite(), include_live=True)
        self.assertEqual(selected.countTestCases(), 4)
        self.assertEqual(excluded, [])


if __name__ == "__main__":
    unittest.main()
