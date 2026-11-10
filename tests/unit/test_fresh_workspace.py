from __future__ import annotations

import importlib
import os
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CHECK_LOCAL_ENV = os.environ.get("FABRIC_CHECK_LOCAL_ENV") == "1"
LOCAL_ENV_REASON = "Optional local setup check; enable with scripts/run_tests.py --local-env"


class FreshWorkspaceTest(unittest.TestCase):
    def test_supported_python_version(self) -> None:
        self.assertGreaterEqual(sys.version_info[:2], (3, 12))
        self.assertLess(sys.version_info[:2], (3, 14))

    @unittest.skipUnless(CHECK_LOCAL_ENV, LOCAL_ENV_REASON)
    def test_project_uses_root_venv(self) -> None:
        expected = (ROOT / ".venv").resolve()

        self.assertNotEqual(sys.prefix, sys.base_prefix)
        self.assertEqual(Path(sys.prefix).resolve(), expected)

    def test_expected_layout_exists(self) -> None:
        expected = (
            "apps/web",
            "packages/fabric-domain",
            "packages/fabric-sim",
            "packages/event-contracts",
            "services/gateway",
            "services/planner",
            "services/aggregator",
            "services/evaluator",
            "db/migrations",
            "infra",
            "tests/unit",
            "tests/integration",
            "tests/e2e",
            "tests/chaos",
            "tests/fixtures/topologies",
        )

        missing = [path for path in expected if not (ROOT / path).is_dir()]

        self.assertEqual(missing, [])

    def test_shared_packages_import(self) -> None:
        domain = importlib.import_module("fabric_domain")
        contracts = importlib.import_module("fabric_contracts")

        self.assertEqual(domain.PACKAGE_NAME, "fabric_domain")
        self.assertEqual(contracts.PACKAGE_NAME, "fabric_contracts")

    @unittest.skipUnless(CHECK_LOCAL_ENV, LOCAL_ENV_REASON)
    def test_environment_check_passes(self) -> None:
        completed = subprocess.run(
            [sys.executable, "scripts/check_env.py"],
            cwd=ROOT,
            capture_output=True,
            check=False,
            text=True,
            env={**os.environ, "PYTHONUTF8": "1"},
            timeout=15,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)
        self.assertIn("S0 environment is ready", completed.stdout)

    def test_environment_configuration_files_exist(self) -> None:
        self.assertTrue((ROOT / ".env.example").is_file())
        self.assertTrue((ROOT / ".python-version").is_file())

    @unittest.skipUnless(CHECK_LOCAL_ENV, LOCAL_ENV_REASON)
    def test_optional_local_configuration_files_exist(self) -> None:
        self.assertTrue((ROOT / ".env").is_file())
        self.assertTrue((ROOT / ".vscode" / "settings.json").is_file())


if __name__ == "__main__":
    unittest.main()

