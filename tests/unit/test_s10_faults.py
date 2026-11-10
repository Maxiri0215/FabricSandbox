from __future__ import annotations

import json
import unittest
from dataclasses import replace
from pathlib import Path

from fabric_domain import (
    Fault,
    FaultError,
    FaultType,
    Node,
    apply_faults,
    load_revision_file,
    replan_after_faults,
    FirstFitPlacementStrategy,
)


ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests" / "fixtures" / "topologies" / "fault-replan.json"


class S10FaultReplanningTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.content = load_revision_file(FIXTURE)

    def test_link_fault_removes_only_target_link(self) -> None:
        fault = Fault(FaultType.LINK_DOWN, "spine-1--leaf-1")

        application = apply_faults(self.content.topology, (fault,))

        self.assertEqual(application.removed_link_ids, (fault.target_id,))
        self.assertEqual(len(application.topology.links), 8)
        self.assertEqual(application.unavailable_node_ids, ())

    def test_single_spine_link_failure_replans_without_migration(self) -> None:
        fault = Fault(FaultType.LINK_DOWN, "spine-0--leaf-1")

        result = replan_after_faults(self.content, (fault,))

        self.assertEqual(result.affected_job_ids, ())
        self.assertEqual(result.migrations, ())
        self.assertTrue(result.placement.complete)
        self.assertEqual(result.evaluation.unroutable_flow_count, 0)

    def test_node_fault_replans_affected_jobs_onto_remaining_capacity(self) -> None:
        fault = Fault(FaultType.NODE_DOWN, "node-1")

        result = replan_after_faults(self.content, (fault,))
        after_by_job = {
            assignment.job_id: assignment
            for assignment in result.placement.assignments
        }

        self.assertEqual(result.application.removed_node_ids, ("node-1",))
        self.assertTrue(result.affected_job_ids)
        self.assertTrue(result.placement.complete)
        self.assertEqual(result.evaluation.unroutable_flow_count, 0)
        for assignment in after_by_job.values():
            self.assertNotIn("node-1", assignment.node_ids)
        self.assertTrue(result.migrations)

    def test_leaf_switch_fault_marks_attached_node_unavailable(self) -> None:
        fault = Fault(FaultType.SWITCH_DOWN, "leaf-1")

        application = apply_faults(self.content.topology, (fault,))

        self.assertIn("node-1", application.unavailable_node_ids)

    def test_unknown_fault_target_is_rejected(self) -> None:
        fault = Fault(FaultType.NODE_DOWN, "missing-node")

        with self.assertRaisesRegex(FaultError, "Unknown node"):
            apply_faults(self.content.topology, (fault,))

    def test_fault_scenario_is_deterministic(self) -> None:
        faults = (
            Fault(FaultType.NODE_DOWN, "node-1"),
            Fault(FaultType.LINK_DOWN, "spine-0--leaf-2"),
        )

        first = replan_after_faults(self.content, faults)
        second = replan_after_faults(self.content, faults)

        self.assertEqual(first.to_dict(), second.to_dict())

    def test_candidate_strategies_use_same_actual_deployment(self) -> None:
        generated = FirstFitPlacementStrategy().place(self.content.topology, self.content.jobs)
        def swap_node(node_id):
            return {"node-0": "node-2", "node-2": "node-0"}.get(node_id, node_id)
        deployed = replace(generated, strategy="actually_deployed", assignments=tuple(
            replace(assignment, node_ids=tuple(swap_node(x) for x in assignment.node_ids),
                    gpus=tuple(replace(gpu, node_id=swap_node(gpu.node_id),
                                       gpu_id=gpu.gpu_id.replace(gpu.node_id, swap_node(gpu.node_id)))
                               for gpu in assignment.gpus), quality=None)
            for assignment in generated.assignments))
        fault = Fault(FaultType.NODE_DOWN, "node-2")
        for strategy in ("first_fit", "rail_aware"):
            result = replan_after_faults(self.content, (fault,), strategy_name=strategy,
                                         current_placement=deployed)
            self.assertIs(result.original_placement, deployed)
            self.assertEqual(result.affected_job_ids, ("job-multi-01",))
            self.assertEqual(result.unaffected_job_ids, ("job-small-02",))
            old_small = next(a for a in deployed.assignments if a.job_id == "job-small-02")
            new_small = next(a for a in result.placement.assignments if a.job_id == "job-small-02")
            self.assertEqual(old_small, new_small)

    def test_stale_current_placement_is_rejected(self) -> None:
        placement = FirstFitPlacementStrategy().place(self.content.topology, self.content.jobs)
        with self.assertRaisesRegex(FaultError, "topology snapshot"):
            replan_after_faults(self.content, (), current_placement=replace(placement, topology_id="other"))

    def test_partial_current_group_is_rejected(self) -> None:
        placement = FirstFitPlacementStrategy().place(self.content.topology, self.content.jobs)
        partial = replace(placement.assignments[0], gpus=placement.assignments[0].gpus[:-1])
        with self.assertRaisesRegex(FaultError, "complete GPU groups"):
            replan_after_faults(self.content, (), current_placement=replace(
                placement, assignments=(partial, *placement.assignments[1:])))


if __name__ == "__main__":
    unittest.main()
