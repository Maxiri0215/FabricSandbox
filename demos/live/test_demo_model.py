"""Contract/regression checks for the presentation adapter (standard library)."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import unittest

from demo_model import MODEL_HASH, plan_case, simulate_case


class ModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plan = plan_case({"uplink_gbps": 50})

    def test_vendor_is_original_and_hash_matches_revalidated_kernel(self):
        self.assertEqual(MODEL_HASH, "d844734439ed524fe81d843d0065a17510ebf161c5efc036f31cf4bb061cd4e3")
        base = Path(__file__).resolve().parent / "vendor"
        manifest = json.loads((base / "fabric_sim_source.json").read_text())
        for record in manifest["files"]:
            self.assertEqual(hashlib.sha256((base / "fabric_sim" / record["name"]).read_bytes()).hexdigest(), record["sha256"])

    def test_default_candidates_and_original_regression_values(self):
        plan = self.plan
        self.assertEqual(plan["status"], "planned")
        self.assertEqual(plan["selected_variant"], "compact")
        self.assertEqual(len(plan["candidates"]), 9)
        self.assertEqual(sum(c["admissible"] for c in plan["candidates"]), 3)
        expected = {"baseline": 7137.466470400024, "distributed": 10721.332940800034,
                    "compact": 5343.733235200019, "compact_reverse": 5343.733235200019}
        for variant, value in expected.items():
            self.assertAlmostEqual(plan["comparison"][variant]["metrics"]["makespan_ms"], value)
        for candidate in plan["candidates"]:
            if candidate["admissible"]:
                self.assertTrue(all(c["passed"] for c in candidate["invariant_checks"]))
            else:
                self.assertTrue(any(c["code"] == "exclusive_ownership" and not c["passed"] for c in candidate["checks"]))

    def test_identical_two_job_workload_and_network_bytes(self):
        comparison = self.plan["comparison"]
        self.assertTrue(self.plan["comparison_contract"]["same_workload_hash"])
        self.assertEqual(comparison["distributed"]["workload_hash"], comparison["compact"]["workload_hash"])
        self.assertNotEqual(comparison["baseline"]["workload_hash"], comparison["compact"]["workload_hash"])
        for variant in ["distributed", "compact", "compact_reverse"]:
            self.assertEqual(comparison[variant]["metrics"]["total_completed_transfer_bytes"], 179193323520)
        self.assertEqual(comparison["distributed"]["metrics"]["cross_rack_completed_bytes"], 89596661760)
        self.assertEqual(comparison["compact"]["metrics"]["cross_rack_completed_bytes"], 0)

    def test_uplink_change_recalculates_instead_of_replaying(self):
        low = plan_case({"uplink_gbps": 25})
        high = plan_case({"uplink_gbps": 100})
        self.assertNotEqual(low["input_hash"], high["input_hash"])
        self.assertGreater(low["comparison"]["distributed"]["metrics"]["makespan_ms"], high["comparison"]["distributed"]["metrics"]["makespan_ms"])
        self.assertAlmostEqual(low["comparison"]["compact"]["metrics"]["makespan_ms"], high["comparison"]["compact"]["metrics"]["makespan_ms"])
        self.assertLessEqual(low["comparison"]["distributed"]["metrics"]["uplink_peak_gbps"], 25)

    def test_memory_rejection_and_blocked_node_are_explicit(self):
        memory = plan_case({"memory_gib": 1})
        blocked = plan_case({"blocked_nodes": ["n0"]})
        for plan in [memory, blocked]:
            self.assertEqual(plan["status"], "blocked")
            self.assertIsNone(plan["selected_candidate"])
            self.assertFalse(any(c["admissible"] for c in plan["candidates"]))
        self.assertTrue(all(any(x["code"] == "memory" and not x["passed"] for x in c["checks"]) for c in memory["candidates"]))
        for candidate in blocked["candidates"]:
            capacity = next(c for c in candidate["checks"] if c["code"] == "simultaneous_group_capacity")
            self.assertEqual((capacity["healthy_gpus"], capacity["required_gpus"]), (6, 8))
            if any(g.startswith("n0:") for allocation in candidate["placements"].values() for g in allocation):
                self.assertTrue(any(c["code"] == "ring_connected" and not c["passed"] for c in candidate["checks"]))

    def test_original_fault_paths_keep_failure_and_new_progress(self):
        ddp = simulate_case({"variant": "fault_no_spare"})
        self.assertEqual(ddp["summary"]["completed_jobs"], 2)
        self.assertEqual(ddp["summary"]["lost_steps"], 6)
        self.assertEqual(ddp["summary"]["restarts"], 1)
        self.assertTrue(all(c["passed"] for c in ddp["result"]["invariant_checks"]))
        self.assertTrue(any(e["kind"] == "step_completed" and e["time_ms"] > 3000 for e in ddp["events"]))
        tp = simulate_case({"scenario": "tp", "variant": "fault"})
        self.assertEqual((tp["summary"]["completed"], tp["summary"]["failed"]), (3, 1))
        self.assertEqual(tp["summary"]["stale_callbacks_ignored"], 1)
        self.assertTrue(all(c["passed"] for c in tp["result"]["invariant_checks"]))

    def test_sampling_preserves_full_metrics_and_raw_export(self):
        sampled = self.plan["result"]
        raw = simulate_case({"variant": "distributed", "include_raw": True})["result"]
        self.assertEqual(sampled["metrics"], raw["metrics"])
        self.assertEqual(len(raw["events"]), sampled["raw_counts"]["events"])
        self.assertEqual(len(raw["resource_samples"]), sampled["raw_counts"]["resource_samples"])
        self.assertLess(len(sampled["events"]), len(raw["events"]))
        self.assertLess(len(sampled["resource_samples"]), len(raw["resource_samples"]))
        self.assertEqual(self.plan["diagnosis"]["evidence"]["allocated_gbps"], 50)

    def test_reject_invalid_inputs_without_mutating_valid_input(self):
        for invalid in [[], "", {"uplink_gbps": float("nan")}, {"uplink_gbps": 0}, {"memory_gib": True},
                        {"steps": 1.0}, {"steps": 31}, {"blocked_nodes": ["n9"]}, {"include_raw": "yes"}]:
            with self.subTest(invalid=invalid):
                with self.assertRaises((ValueError, TypeError)):
                    plan_case(invalid)
        payload = {"uplink_gbps": 25, "blocked_nodes": ["n0", "n0"]}
        before = deepcopy(payload)
        plan_case(payload)
        self.assertEqual(payload, before)


if __name__ == "__main__":
    unittest.main(verbosity=2)
