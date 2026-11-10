"""Run portable tests; real TCP/DDP workers and local setup checks are opt-in."""
from __future__ import annotations

import argparse
import os
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOTS = (
    ROOT,
    ROOT / "packages" / "fabric-domain" / "src",
    ROOT / "packages" / "fabric-sim" / "src",
    ROOT / "packages" / "event-contracts" / "src",
    ROOT / "services" / "gateway" / "src",
    ROOT / "services" / "planner" / "src",
    ROOT / "services" / "aggregator" / "src",
    ROOT / "services" / "evaluator" / "src",
)
MINIMUM_PYTHON = (3, 12)
MAXIMUM_PYTHON_EXCLUSIVE = (3, 14)
LIVE_TEST_CLASSES = {
    "tests.agent.test_tcp.TCPLiveTests": "starts real TCP workers and binds loopback ports",
    "tests.agent.test_ddp.LiveCPUDDPTests": "starts CPU DDP workers and binds a rendezvous port",
}


def filter_suite(suite: unittest.TestSuite, *, include_live: bool = False):
    """Preserve all ordinary tests, including import failures, while listing opt-ins."""
    selected = unittest.TestSuite()
    excluded = []
    for test in suite:
        if isinstance(test, unittest.TestSuite):
            nested, skipped = filter_suite(test, include_live=include_live)
            selected.addTests(nested)
            excluded.extend(skipped)
            continue
        test_id = test.id()
        reason = LIVE_TEST_CLASSES.get(test_id.rsplit(".", 1)[0])
        if reason and not include_live:
            excluded.append((test_id, reason))
        else:
            selected.addTest(test)
    return selected, excluded


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run unittest with the repository source paths and current Python interpreter.",
        epilog="Requires the dependencies used by the selected tests. "
               "No project-local virtual environment is required.",
    )
    parser.add_argument(
        "--live", action="store_true",
        help="Include real TCP and CPU DDP worker tests (binds local ports).",
    )
    parser.add_argument(
        "--local-env", action="store_true",
        help="Also check the optional project .venv, local .env, and VS Code settings.",
    )
    parser.add_argument(
        "tests", nargs="*", metavar="TEST",
        help="Optional dotted test modules/classes/methods; otherwise discover tests/.",
    )
    args = parser.parse_args()
    if not MINIMUM_PYTHON <= sys.version_info[:2] < MAXIMUM_PYTHON_EXCLUSIVE:
        print(
            "FabricSandbox requires Python 3.12 or 3.13; "
            f"current interpreter is {sys.version.split()[0]}."
        )
        return 2

    os.chdir(ROOT)
    os.environ["PYTHONUTF8"] = "1"
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    sys.dont_write_bytecode = True
    if args.local_env:
        os.environ["FABRIC_CHECK_LOCAL_ENV"] = "1"
    pythonpath = [str(path) for path in SOURCE_ROOTS]
    sys.path[:0] = pythonpath
    existing_pythonpath = os.environ.get("PYTHONPATH")
    os.environ["PYTHONPATH"] = os.pathsep.join(
        pythonpath + ([existing_pythonpath] if existing_pythonpath else [])
    )

    loader = unittest.TestLoader()
    suite = (
        loader.loadTestsFromNames(args.tests)
        if args.tests
        else loader.discover(str(ROOT / "tests"), pattern="test_*.py", top_level_dir=str(ROOT))
    )
    suite, excluded = filter_suite(suite, include_live=args.live)
    for test_id, reason in excluded:
        print(f"[not run; use --live] {test_id}: {reason}", flush=True)
    print(f"Running {suite.countTestCases()} tests; {len(excluded)} live tests not selected.", flush=True)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())

