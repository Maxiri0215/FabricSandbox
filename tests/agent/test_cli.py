from __future__ import annotations

import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from agent_demo.__main__ import monitor_with_injector, run_tcp
from agent_demo.common import atomic_json
from agent_demo.tcp_backend import TCPAdapter
from agent_demo.tcp_worker import Receiver
from scripts.run_agent_gpu_matrix import main as matrix_main


class InMemoryTCP(TCPAdapter):
    """Use the REAL adapter/protocol with transport replaced; never live evidence."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.online = {"receiver": True, "A": True, "B": True}
        self.receiver = Receiver(self.output / "receiver_state.json")

    def start(self):
        self._started = True
        self.endpoints = {"receiver": 1, "A": 2, "B": 3}
        return {"source": "TEST_DOUBLE", "active": self.active}

    def _stop(self, name):
        self.online[name] = False

    def exchange(self, port, request, timeout):
        target = next(k for k, v in self.endpoints.items() if v == port)
        if not self.online[target] or not self.online["receiver"]:
            raise ConnectionRefusedError("TEST DOUBLE channel unavailable")
        return self.receiver.request(request)


class CLITest(unittest.TestCase):
    def test_monitored_tcp_driver_with_in_memory_protocol(self):
        with tempfile.TemporaryDirectory() as folder:
            for scenario, expected in (("recover", "recovered"), ("blocked", "blocked"),
                                       ("receiver-down", "inconclusive"), ("baseline", "healthy"),
                                       ("no-repair", "unrecovered_control")):
                output = Path(folder) / scenario
                adapter = InMemoryTCP(output / "backend")
                args = SimpleNamespace(scenario=scenario, timeout=1, initial_channel="A", mode="rules")
                with patch("agent_demo.tcp_backend.TCPAdapter", return_value=adapter), \
                     patch("agent_demo.tcp_backend.exchange", side_effect=adapter.exchange):
                    result = run_tcp(args, output)
                self.assertEqual(result["status"], expected, result)
                self.assertTrue(all(x["passed"] for x in result["checks"]))
                if scenario in {"recover", "blocked", "receiver-down"}:
                    self.assertGreaterEqual(result["detection_delay_ms"], 0)
                    self.assertIn("anomaly_detected", [x["kind"] for x in result["incident"]["events"]])

    def test_gpu_matrix_stops_after_failed_acceptance(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "matrix"
            commands = []
            def run(command, **kwargs):
                commands.append(command)
                destination = Path(command[command.index("--output") + 1])
                atomic_json(destination / "result.json", {"status": "failed", "checks": [{"passed": False}]})
                return SimpleNamespace(returncode=2)
            argv = ["run_agent_gpu_matrix.py", "--gpus", "0,1,2", "--output", str(output)]
            with patch("sys.argv", argv), patch("scripts.run_agent_gpu_matrix.subprocess.run", side_effect=run):
                self.assertEqual(matrix_main(), 2)
            self.assertEqual(len(commands), 1)
            self.assertIn("tiny", commands[0])


if __name__ == "__main__":
    unittest.main()
