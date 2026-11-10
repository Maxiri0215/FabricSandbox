"""Custom topology contract, actual engine effects, and legacy compatibility."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from demo_model import default_topology, plan_case, simulate_case, validate_topology


def three_racks():
    return {"nodes": [{"id": f"host-{i}", "rack_id": f"cabinet-{i}",
                       "gpu_count": 4, "memory_gib": 24} for i in range(3)],
            "node_bandwidth_gbps": 100, "uplink_bandwidth_gbps": 25,
            "local_bandwidth_gbps": 200, "link_latency_us": 5, "local_latency_us": 2}


class CustomTopologyTests(unittest.TestCase):
    def test_validation_defaults_and_wrapper_are_identical_without_mutation(self):
        top = {"nodes": [{"id": "host", "rack_id": "cabinet", "gpu_count": 8}]}
        before = deepcopy(top)
        direct = validate_topology(top)
        self.assertEqual(direct, validate_topology({"topology": top}))
        self.assertEqual(direct["summary"], {"racks": 1, "nodes": 1, "gpus": 8})
        self.assertEqual(direct["topology"]["uplink_bandwidth_gbps"], 50)
        self.assertEqual(direct["topology"]["nodes"][0]["memory_gib"], 24)
        self.assertEqual(top, before)
        self.assertEqual(default_topology()["summary"], {"racks": 2, "nodes": 4, "gpus": 8})

    def test_bad_topologies_rejected_before_simulation(self):
        top = three_racks()
        cases = [[], {}, {"nodes": []}, {**top, "edges": []},
                 {**top, "uplink_bandwidth_gbps": float("nan")},
                 {**top, "link_latency_us": -1},
                 {"topology": top, "unexpected": 1}]
        for override in ({"id": "host:bad"}, {"id": "a" * 49}, {"gpu_count": True},
                         {"gpu_count": 9}, {"gpu_count": 1.5}, {"memory_gib": 0},
                         {"rack_id": "<rack>"}, {"gpus": 4}):
            bad = deepcopy(top)
            bad["nodes"][0].update(override)
            cases.append(bad)
        cases.append({"nodes": [top["nodes"][0], top["nodes"][0]]})
        cases.append({"nodes": [{"id": f"n{i}", "rack_id": "r", "gpu_count": 8}
                                 for i in range(9)]})
        cases.append({"nodes": [{"id": f"n{i}", "rack_id": "r", "gpu_count": 1}
                                 for i in range(17)]})
        for bad in cases:
            with self.subTest(topology=bad):
                with self.assertRaises(ValueError):
                    validate_topology(bad)

    def test_three_rack_plan_and_application_keep_exact_frozen_inputs(self):
        request = {"topology": three_racks(), "steps": 2}
        before = deepcopy(request)
        plan = plan_case(request)
        self.assertEqual(plan["status"], "planned")
        self.assertEqual(len(plan["candidates"]), 3)
        self.assertEqual(len({n["rack_id"] for n in plan["topology"]["nodes"]}), 3)
        hashes = set()
        for candidate in plan["candidates"]:
            self.assertTrue(candidate["admissible"])
            result = simulate_case({**plan["request"], "variant": candidate["variant"]})
            self.assertEqual(result["input_hash"], candidate["input_hash"])
            self.assertEqual(result["placements"], candidate["placements"])
            self.assertEqual(result["topology"], plan["topology"])
            self.assertTrue(all(c["passed"] for c in result["result"]["invariant_checks"]))
            self.assertEqual(result["summary"]["completed_jobs"], 2)
            hashes.add(result["workload_hash"])
        self.assertEqual(len(hashes), 1)
        self.assertEqual(request, before)

    def test_uplink_and_rack_assignment_change_engine_results(self):
        top = three_racks()
        low = simulate_case({"topology": top, "steps": 2})
        high_top = deepcopy(top)
        high_top["uplink_bandwidth_gbps"] = 100
        high = simulate_case({"topology": high_top, "steps": 2})
        self.assertGreater(low["metrics"]["makespan_ms"], high["metrics"]["makespan_ms"])
        self.assertGreater(low["metrics"]["cross_rack_completed_bytes"], 0)
        local_top = deepcopy(top)
        for node in local_top["nodes"]:
            node["rack_id"] = "same-cabinet"
        local = simulate_case({"topology": local_top, "steps": 2})
        self.assertEqual(local["metrics"]["cross_rack_completed_bytes"], 0)
        self.assertNotEqual(local["input_hash"], low["input_hash"])

    def test_single_node_and_maximum_topology_work(self):
        for top in [
            {"nodes": [{"id": "only-host", "rack_id": "one", "gpu_count": 8}]},
            {"nodes": [{"id": f"host-{i:02d}", "rack_id": f"rack-{i % 4}", "gpu_count": 4}
                       for i in range(16)]}]:
            with self.subTest(nodes=len(top["nodes"])):
                plan = plan_case({"topology": top, "steps": 1})
                self.assertEqual(plan["status"], "planned")
                self.assertEqual(plan["selected_candidate"]["metrics"]["completed_jobs"], 2)

    def test_health_and_memory_constraints_and_spare_selection(self):
        top = three_racks()
        recovered = plan_case({"topology": top, "blocked_nodes": ["host-0"], "steps": 1})
        self.assertEqual(recovered["status"], "planned")
        chosen = recovered["selected_candidate"]["placements"]
        self.assertFalse(any(g.startswith("host-0:") for alloc in chosen.values() for g in alloc))
        blocked = plan_case({"topology": top, "blocked_nodes": ["host-0", "host-1"], "steps": 1})
        self.assertEqual(blocked["status"], "blocked")
        self.assertIsNone(blocked["selected_candidate"])
        for node in top["nodes"]:
            node["memory_gib"] = 1
        memory = plan_case({"topology": top, "steps": 1})
        self.assertEqual(memory["status"], "blocked")
        self.assertTrue(all(any(c["code"] == "memory" and not c["passed"] for c in candidate["checks"])
                            for candidate in memory["candidates"]))

    def test_custom_ambiguity_unknown_options_and_small_planning_rejected(self):
        top = three_racks()
        for options in [{"uplink_gbps": 50}, {"memory_gib": 24}, {"scenario": "tp"},
                        {"blocked_nodes": ["n0"]}, {"steps": 0}, {"include_raw": "yes"},
                        {"toplogy": top}]:
            with self.subTest(options=options):
                with self.assertRaises(ValueError):
                    plan_case({"topology": top, **options})
        with self.assertRaises(ValueError):
            simulate_case({"topology": top, "variant": "fault_no_spare"})
        tiny = {"nodes": [{"id": "n", "rack_id": "r", "gpu_count": 1}]}
        self.assertEqual(validate_topology(tiny)["summary"]["gpus"], 1)
        with self.assertRaisesRegex(ValueError, "至少需要8"):
            plan_case({"topology": tiny})

    def test_partial_completion_has_no_makespan_or_recommendation(self):
        top = {"nodes": [{"id": "small", "rack_id": "r0", "gpu_count": 1},
                         {"id": "large", "rack_id": "r1", "gpu_count": 7}],
               "uplink_bandwidth_gbps": .001}
        plan = plan_case({"topology": top})
        self.assertEqual(plan["status"], "blocked")
        self.assertIsNone(plan["selected_candidate"])
        for candidate in plan["candidates"]:
            self.assertTrue(candidate["admissible"])
            self.assertEqual(candidate["metrics"]["completed_jobs"], 1)
            self.assertFalse(candidate["metrics"]["all_jobs_completed"])
            self.assertIsNone(candidate["metrics"]["makespan_ms"])
        run = simulate_case({"topology": top, "variant": "compact"})
        self.assertEqual(run["metrics"]["completed_jobs"], 1)
        self.assertIsNone(run["metrics"]["makespan_ms"])

    def test_extremely_large_numeric_values_are_validation_errors(self):
        huge = 10 ** 500
        with self.assertRaises(ValueError):
            validate_topology({**three_racks(), "uplink_bandwidth_gbps": huge})
        with self.assertRaises(ValueError):
            plan_case({"uplink_gbps": huge})
        with self.assertRaises(ValueError):
            plan_case({"topology": three_racks(), "steps": huge})


if __name__ == "__main__":
    unittest.main()
