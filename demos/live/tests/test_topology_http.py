"""HTTP contracts for editable topology; no TCP processes or runtime files."""
from __future__ import annotations

from copy import deepcopy
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import sys
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from server import Console, handler


def three_racks():
    return {
        "nodes": [
            {"id": f"worker-{i}", "rack_id": f"rack-{i // 2}",
             "gpu_count": 2, "memory_gib": 24}
            for i in range(6)
        ],
        "node_bandwidth_gbps": 100,
        "uplink_bandwidth_gbps": 50,
        "local_bandwidth_gbps": 200,
        "link_latency_us": 5,
        "local_latency_us": 2,
    }


class TopologyHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.console = Console()
        request_handler = handler(cls.console)
        request_handler.log_message = lambda *_args: None
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), request_handler)
        cls.server.daemon_threads = True
        cls.thread = threading.Thread(
            target=cls.server.serve_forever, kwargs={"poll_interval": .05}, daemon=True
        )
        cls.thread.start()
        cls.url = f"http://127.0.0.1:{cls.server.server_port}"
        with urlopen(cls.url + "/api/config", timeout=10) as response:
            cls.token = json.loads(response.read())["token"]

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)
        cls.console.close()

    def http(self, method, path, payload=None, *, token=True):
        headers, data = {}, None
        if method == "POST":
            headers["Content-Type"] = "application/json"
            if token:
                headers["X-Demo-Token"] = self.token
            data = json.dumps(payload if payload is not None else {}).encode()
        request = Request(self.url + path, data=data, headers=headers, method=method)
        try:
            with urlopen(request, timeout=35) as response:
                return response.status, json.loads(response.read())
        except HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def test_default_entry_preserves_two_racks_and_original_result(self):
        code, default = self.http("GET", "/api/topology/default")
        self.assertEqual(code, 200, default)
        self.assertEqual(default["summary"], {"racks": 2, "nodes": 4, "gpus": 8})
        code, plan = self.http("POST", "/api/planning/analyze", {})
        self.assertEqual(code, 200, plan)
        self.assertEqual(plan["topology"], default["topology"])
        self.assertEqual(plan["selected_variant"], "compact")
        self.assertEqual(len(plan["candidates"]), 9)
        self.assertAlmostEqual(
            plan["comparison"]["distributed"]["metrics"]["makespan_ms"],
            10721.332940800034,
        )
        self.assertAlmostEqual(
            plan["comparison"]["compact"]["metrics"]["makespan_ms"],
            5343.733235200019,
        )

    def test_three_racks_apply_uses_frozen_topology_and_exports_it(self):
        topology = three_racks()
        code, checked = self.http("POST", "/api/topology/validate", {"topology": topology})
        self.assertEqual(code, 200, checked)
        self.assertEqual(checked["summary"], {"racks": 3, "nodes": 6, "gpus": 12})
        code, plan = self.http("POST", "/api/planning/analyze", {"topology": topology, "steps": 3})
        self.assertEqual(code, 200, plan)
        self.assertEqual(plan["status"], "planned")
        self.assertEqual(plan["topology"], checked["topology"])
        self.assertTrue(plan["comparison_contract"]["same_workload_hash"])
        self.assertTrue(all(g.startswith("worker-") for allocation in plan["placements"].values() for g in allocation))
        selection = {"run_id": plan["run_id"], "candidate_id": plan["selected_candidate_id"],
                     "topology": {"nodes": []}, "steps": 1}
        code, applied = self.http("POST", "/api/planning/apply", selection)
        self.assertEqual(code, 200, applied)
        evaluation = applied["evaluation"]
        self.assertEqual(evaluation["topology"], checked["topology"])
        self.assertEqual(evaluation["input_hash"], plan["selected_candidate"]["input_hash"])
        self.assertEqual(evaluation["workload_hash"], plan["workload_hash"])
        self.assertTrue(all(j["steps"] == 3 for j in evaluation["jobs"]))
        self.assertTrue(evaluation["metrics"]["all_jobs_completed"])
        self.assertTrue(all(c["passed"] for c in evaluation["result"]["invariant_checks"]))
        code, repeated = self.http("POST", "/api/planning/apply", selection)
        self.assertEqual(code, 200, repeated)
        self.assertEqual(repeated["executed_at"], applied["executed_at"])
        code, exported = self.http("GET", "/api/export")
        self.assertEqual(code, 200, exported)
        self.assertEqual(exported["network"]["payload"]["topology"], topology)
        self.assertEqual(exported["network"]["analysis"]["run_id"], plan["run_id"])
        self.assertEqual(exported["network"]["execution"]["evaluation"]["topology"], checked["topology"])
        self.assertEqual(exported["tcp"]["status"], "idle")

    def test_one_rack_runs_without_cross_rack_traffic(self):
        topology = {"nodes": [
            {"id": "alpha", "rack_id": "only", "gpu_count": 4},
            {"id": "beta", "rack_id": "only", "gpu_count": 4},
        ]}
        code, checked = self.http("POST", "/api/topology/validate", topology)
        self.assertEqual(code, 200, checked)
        self.assertEqual(checked["summary"], {"racks": 1, "nodes": 2, "gpus": 8})
        code, plan = self.http("POST", "/api/planning/analyze", {"topology": topology, "steps": 2})
        self.assertEqual(code, 200, plan)
        self.assertEqual(plan["status"], "planned")
        self.assertEqual(plan["selected_candidate"]["metrics"]["cross_rack_completed_bytes"], 0)
        self.assertEqual(plan["selected_candidate"]["metrics"]["uplink_peak_gbps"], 0)

    def test_capacity_change_recomputes_and_changes_input_hash(self):
        slow = three_racks()
        slow["uplink_bandwidth_gbps"] = 25
        fast = deepcopy(slow)
        fast["uplink_bandwidth_gbps"] = 100
        code, a = self.http("POST", "/api/planning/analyze", {"topology": slow, "steps": 3})
        self.assertEqual(code, 200, a)
        code, b = self.http("POST", "/api/planning/analyze", {"topology": fast, "steps": 3})
        self.assertEqual(code, 200, b)
        self.assertNotEqual(a["input_hash"], b["input_hash"])
        self.assertNotEqual(a["workload_hash"], b["workload_hash"])
        self.assertGreater(a["comparison"]["distributed"]["metrics"]["makespan_ms"],
                           b["comparison"]["distributed"]["metrics"]["makespan_ms"])
        self.assertLessEqual(a["comparison"]["distributed"]["metrics"]["uplink_peak_gbps"], 25)

    def test_maximum_supported_topology_and_normalization(self):
        topology = {"nodes": [
            {"id": f"server{i}", "rack_id": f"cabinet{i // 4}", "gpu_count": 4}
            for i in range(16)
        ]}
        code, checked = self.http("POST", "/api/topology/validate", topology)
        self.assertEqual(code, 200, checked)
        self.assertEqual(checked["summary"], {"racks": 4, "nodes": 16, "gpus": 64})
        self.assertEqual(checked["topology"]["uplink_bandwidth_gbps"], 50)
        self.assertTrue(all(n["memory_gib"] == 24 for n in checked["topology"]["nodes"]))
        code, wrapped = self.http("POST", "/api/topology/validate", {"topology": checked["topology"]})
        self.assertEqual(code, 200, wrapped)
        self.assertEqual(wrapped["topology_hash"], checked["topology_hash"])

    def test_invalid_topologies_and_mixed_configuration_return_400(self):
        duplicate = three_racks()
        duplicate["nodes"][1]["id"] = duplicate["nodes"][0]["id"]
        unknown = three_racks()
        unknown["uplink_bandwith_gbps"] = 25
        nonfinite = three_racks()
        nonfinite["uplink_bandwidth_gbps"] = float("nan")
        invalid_id = three_racks()
        invalid_id["nodes"][0]["id"] = "node:ambiguous"
        for topology in [duplicate, unknown, nonfinite, invalid_id, {"nodes": []}]:
            with self.subTest(topology=topology):
                code, error = self.http("POST", "/api/topology/validate", {"topology": topology})
                self.assertEqual(code, 400, error)
                self.assertTrue(error.get("error"))
        for key, value in [("uplink_gbps", 50), ("memory_gib", 24)]:
            with self.subTest(mixed=key):
                code, error = self.http("POST", "/api/planning/analyze", {"topology": three_racks(), key: value})
                self.assertEqual(code, 400, error)
        code, error = self.http("POST", "/api/planning/analyze", {
            "topology": three_racks(), "blocked_nodes": ["n0"]})
        self.assertEqual(code, 400, error)

    def test_incomplete_candidate_cannot_apply_but_completed_candidate_can(self):
        code, default = self.http("GET", "/api/topology/default")
        self.assertEqual(code, 200, default)
        topology = default["topology"]
        topology["uplink_bandwidth_gbps"] = .001
        code, plan = self.http("POST", "/api/planning/analyze", {"topology": topology})
        self.assertEqual(code, 200, plan)
        distributed = next(c for c in plan["candidates"] if c["id"] == "distributed")
        self.assertTrue(distributed["admissible"])
        self.assertEqual(distributed["metrics"]["completed_jobs"], 0)
        self.assertFalse(distributed["metrics"]["all_jobs_completed"])
        self.assertIsNone(distributed["metrics"]["makespan_ms"])
        code, error = self.http("POST", "/api/planning/apply", {
            "run_id": plan["run_id"], "candidate_id": distributed["id"]})
        self.assertEqual(code, 400, error)
        self.assertIn("未完成", error["error"])
        compact = next(c for c in plan["candidates"] if c["id"] == "compact")
        self.assertTrue(compact["metrics"]["all_jobs_completed"])
        code, applied = self.http("POST", "/api/planning/apply", {
            "run_id": plan["run_id"], "candidate_id": compact["id"]})
        self.assertEqual(code, 200, applied)
        self.assertEqual(applied["candidate_id"], "compact")
        self.assertTrue(applied["evaluation"]["metrics"]["all_jobs_completed"])
        self.assertEqual(applied["evaluation"]["input_hash"], compact["input_hash"])

    def test_validation_keeps_existing_post_authentication(self):
        code, error = self.http("POST", "/api/topology/validate", {"topology": three_racks()}, token=False)
        self.assertEqual(code, 403, error)


if __name__ == "__main__":
    unittest.main(verbosity=2)
