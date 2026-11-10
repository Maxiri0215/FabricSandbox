from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from agent_demo.legacy_bridge import import_profile, run_planning_demo, _validated_a1


def sample_a1() -> dict:
    return {
        "status": "COMPLETED", "source": "MEASURED", "kind": "gpt2_compute_profile",
        "parameter_count": 124439808, "gradient_bytes": 497759232,
        "parameter_dtype": "float32", "gradient_dtype": "float32", "optimizer": "AdamW(foreach=False)",
        "config": {"n_layer": 12, "n_embd": 768, "n_head": 12, "vocab_size": 50257,
                   "n_positions": 1024, "n_ctx": 1024, "n_inner": 3072,
                   "tie_word_embeddings": True, "use_cache": False},
        "local_batch": 1, "sequence_length": 512, "warmup": 1, "repeat": 2,
        "samples": [{"forward_backward_ms": 80., "optimizer_ms": 5.},
                    {"forward_backward_ms": 100., "optimizer_ms": 7.}],
        "timing": {"forward_backward_ms": {"median_ms": 90., "count": 2},
                   "optimizer_ms": {"median_ms": 6., "count": 2}},
        "memory": {"peak_allocated_bytes": 3 * 2**30, "peak_reserved_bytes": 4 * 2**30,
                   "configured_margin_bytes": 2**30, "admission_budget_bytes": 5 * 2**30},
    }


class LegacyBridgeTests(unittest.TestCase):
    def test_import_runs_two_simulations_with_honest_provenance(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "profile.json"
            source.write_text(json.dumps(sample_a1()), encoding="utf-8")
            report = import_profile(source, root / "out")
            self.assertEqual(report["backend"], "simulation")
            self.assertTrue(all(report["checks"].values()))
            self.assertEqual(len(report["records"]), 2)
            self.assertTrue(all(record["all_jobs_completed"] for record in report["records"]))
            for field in ("checkpoint_save_ms", "restore_ms", "group_init_ms"):
                self.assertEqual(report["profile"]["field_sources"][field], "SYNTHETIC")
            self.assertEqual(source.read_bytes(), (root / "out" / "source_profile.json").read_bytes())
            specs = [json.loads(Path(record["input"]).read_text()) for record in report["records"]]
            self.assertEqual(specs[0]["profile"], specs[1]["profile"])
            self.assertNotIn("restore_ms", specs[0]["profile"])

    def test_import_rejects_standalone_collective(self):
        data = sample_a1()
        data["kind"] = "nccl_allreduce"
        with self.assertRaisesRegex(ValueError, "kind"):
            _validated_a1(data)

    def test_import_rejects_corrupt_summary_and_impossible_memory(self):
        data = sample_a1()
        data["timing"]["optimizer_ms"]["median_ms"] = 600
        with self.assertRaisesRegex(ValueError, "summary"):
            _validated_a1(data)
        data = sample_a1()
        data["memory"]["admission_budget_bytes"] = 1
        with self.assertRaisesRegex(ValueError, "memory"):
            _validated_a1(data)

    def test_import_rejects_nonfinite_samples_and_wrong_model(self):
        for mutation in ("nan", "config"):
            data = sample_a1()
            if mutation == "nan":
                data["samples"][0]["optimizer_ms"] = float("nan")
            else:
                data["config"]["n_layer"] = 2
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                _validated_a1(data)

    def test_planning_candidates_share_fixed_baseline(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = run_planning_demo(Path(tmp))
            self.assertEqual(report["backend"], "planning_proxy")
            self.assertTrue(all(report["checks"].values()))
            self.assertFalse(report["baseline"]["metrics"]["feasible"])
            self.assertIn(report["selected_candidate"], ("first_fit", "rail_aware"))
            for candidate in report["candidates"]:
                self.assertEqual(candidate["current_placement_hash"], report["current_placement_hash"])
                self.assertTrue(candidate["metrics"]["feasible"])
            with self.assertRaisesRegex(ValueError, "replace previous evidence"):
                run_planning_demo(Path(tmp))


if __name__ == "__main__":
    unittest.main()
