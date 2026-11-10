"""Reuse the original planner and A1 measurements without changing their scope.

No torch/CUDA is imported. Network planning proxies and calibrated simulation
results are deliberately labelled separately from live execution evidence.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import statistics
import sys

from .common import atomic_json, canonical_hash


ROOT = Path(__file__).resolve().parents[1]


def _domain_paths() -> None:
    for package in ("fabric-domain", "fabric-sim"):
        path = ROOT / "packages" / package / "src"
        if not path.is_dir():
            raise ValueError(f"Required existing package is missing: {path}")
        if str(path) not in sys.path:
            sys.path.insert(0, str(path))


def _output(path: Path) -> Path:
    path = Path(path).expanduser().resolve()
    path.mkdir(parents=True, exist_ok=True)
    if (path / "report.json").exists():
        raise ValueError(f"Refusing to replace previous evidence: {path / 'report.json'}")
    return path


def _planning_metrics(placement, evaluation, topology) -> dict:
    available = {gpu.id for node in topology.nodes for gpu in node.gpus}
    invalid = [gpu.gpu_id for assignment in placement.assignments for gpu in assignment.gpus
               if gpu.gpu_id not in available]
    return {
        "unplaced_jobs": list(placement.unplaced_job_ids),
        "invalid_gpu_assignments": invalid,
        "unroutable_flows": evaluation.unroutable_flow_count,
        "oversubscribed_links": list(evaluation.oversubscribed_link_ids),
        "job_throughput_proxy_gbps": {job.job_id: job.throughput_proxy_gbps for job in evaluation.jobs},
        "feasible": placement.complete and not invalid and evaluation.unroutable_flow_count == 0,
    }


def run_planning_demo(output: Path) -> dict:
    """Compare two fault-replanning heuristics from ONE frozen actual placement."""
    _domain_paths()
    from fabric_domain import (EvaluationConfig, Fault, FaultType, FirstFitPlacementStrategy,
                               apply_faults, evaluate_placement, load_revision_file,
                               replan_after_faults)
    output = _output(output)
    fixture = ROOT / "tests" / "fixtures" / "topologies" / "fault-replan.json"
    if not fixture.is_file():
        raise ValueError(f"Original planning fixture is missing: {fixture}")
    content = load_revision_file(fixture)
    current = FirstFitPlacementStrategy().place(content.topology, content.jobs)
    faults = (Fault(FaultType.NODE_DOWN, "node-1"),)
    application = apply_faults(content.topology, faults)
    config = EvaluationConfig(flow_unit_gbps=10.0)
    baseline_eval = evaluate_placement(application.topology, content.jobs, current, config)
    baseline = {"placement": current.to_dict(), "evaluation": baseline_eval.to_dict(),
                "metrics": _planning_metrics(current, baseline_eval, application.topology)}
    snapshot = {"revision": content.to_dict(), "current_placement": current.to_dict(),
                "faults": [fault.to_dict() for fault in faults], "flow_unit_gbps": 10.0}
    snapshot_hash = canonical_hash(snapshot)
    candidates = []
    for strategy in ("first_fit", "rail_aware"):
        result = replan_after_faults(content, faults, strategy_name=strategy,
                                     flow_unit_gbps=10.0, current_placement=current)
        metrics = _planning_metrics(result.placement, result.evaluation, application.topology)
        reasons = []
        if metrics["unplaced_jobs"]:
            reasons.append("insufficient_feasible_gpu_groups")
        if metrics["invalid_gpu_assignments"]:
            reasons.append("assignment_uses_unavailable_gpu")
        if metrics["unroutable_flows"]:
            reasons.append("unroutable_communication")
        moved = sum(m.moved_gpu_count for m in result.migrations)
        candidate = {"id": strategy, "input_hash": snapshot_hash,
                     "current_placement_hash": canonical_hash(result.original_placement.to_dict()),
                     "metrics": metrics, "rejection_reasons": reasons,
                     "moved_gpu_count": moved, "result": result.to_dict()}
        candidates.append(candidate)
        atomic_json(output / f"candidate_{strategy}.json", candidate)
    viable = [candidate for candidate in candidates if not candidate["rejection_reasons"]]
    selected = min(viable, key=lambda c: (c["moved_gpu_count"], c["id"])) if viable else None
    report = {
        "backend": "planning_proxy", "status": "completed", "input_hash": snapshot_hash,
        "current_placement_hash": canonical_hash(current.to_dict()),
        "baseline": baseline, "candidates": candidates,
        "selected_candidate": selected["id"] if selected else None,
        "selection_rule": "Filter infeasible plans; minimize moved GPU count; stable ID breaks ties.",
        "checks": {
            "same_initial_placement": all(c["current_placement_hash"] == canonical_hash(current.to_dict())
                                           for c in candidates),
            "same_scenario": all(c["input_hash"] == snapshot_hash for c in candidates),
            "healthy_jobs_preserved": all(
                all(next(a for a in current.assignments if a.job_id == jid).to_dict() ==
                    next(a for a in c["result"]["placement"]["assignments"] if a["job_id"] == jid)
                    for jid in c["result"]["unaffected_job_ids"])
                for c in candidates),
        },
        "limitations": [
            "Known injected fault is a planning input here; this command does not perform blind live fault diagnosis.",
            "No GPU process is migrated or restarted by this command.",
            "Bandwidth is the original static network proxy, not measured throughput or v2 DDP duration.",
            "Candidate strategies may produce identical plans; no improvement is invented.",
        ],
        "artifacts": {"input": str(output / "input.json"), "baseline": str(output / "baseline.json"),
                      "report": str(output / "report.json")},
    }
    atomic_json(output / "input.json", snapshot)
    atomic_json(output / "baseline.json", baseline)
    atomic_json(output / "report.json", report)
    return report


def _positive(value, name: str, *, integer=False, minimum=0, allow_zero=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{name} must be finite numeric data")
    if integer and (not isinstance(value, int)):
        raise ValueError(f"{name} must be an integer")
    if value < minimum or (value == minimum and not allow_zero):
        raise ValueError(f"{name} must be {'at least' if allow_zero else 'greater than'} {minimum}")
    return value


def _validated_a1(data: dict) -> dict:
    required = {"status": "COMPLETED", "source": "MEASURED", "kind": "gpt2_compute_profile",
                "parameter_count": 124439808, "gradient_bytes": 497759232,
                "parameter_dtype": "float32", "gradient_dtype": "float32",
                "optimizer": "AdamW(foreach=False)"}
    for field, expected in required.items():
        if type(data.get(field)) is not type(expected) or data.get(field) != expected:
            raise ValueError(f"A1 {field} must be {expected!r}")
    config = data.get("config", {})
    if not isinstance(config, dict):
        raise ValueError("A1 config must be an object")
    for key, expected in {"n_layer": 12, "n_embd": 768, "n_head": 12, "vocab_size": 50257,
                          "n_positions": 1024, "n_ctx": 1024, "n_inner": 3072,
                          "tie_word_embeddings": True, "use_cache": False}.items():
        if type(config.get(key)) is not type(expected) or config.get(key) != expected:
            raise ValueError(f"A1 config.{key} must be {expected!r}")
    batch = _positive(data.get("local_batch"), "local_batch", integer=True)
    sequence = _positive(data.get("sequence_length"), "sequence_length", integer=True)
    if batch > 4096 or sequence > 1024:
        raise ValueError("A1 batch or sequence length exceeds the existing simulator contract")
    repeat = _positive(data.get("repeat"), "repeat", integer=True)
    _positive(data.get("warmup"), "warmup", integer=True)
    samples = data.get("samples")
    if not isinstance(samples, list) or len(samples) != repeat or repeat > 1000:
        raise ValueError("A1 samples must match repeat (at most 1000)")
    measured = {}
    timing = data.get("timing", {})
    if not isinstance(timing, dict):
        raise ValueError("A1 timing must be an object")
    for field, target in (("forward_backward_ms", "compute_ms"), ("optimizer_ms", "optimizer_ms")):
        values = [_positive(item.get(field) if isinstance(item, dict) else None, f"samples.{field}")
                  for item in samples]
        stat = timing.get(field, {})
        if not isinstance(stat, dict):
            raise ValueError(f"A1 timing.{field} must be an object")
        median = _positive(stat.get("median_ms"), f"timing.{field}.median_ms")
        if stat.get("count") != repeat or not math.isclose(median, statistics.median(values), rel_tol=1e-9):
            raise ValueError(f"A1 {field} summary disagrees with raw samples")
        if median > 60000:
            raise ValueError("A1 timing exceeds the simulator's per-phase bound")
        measured[target] = median
    memory = data.get("memory", {})
    if not isinstance(memory, dict):
        raise ValueError("A1 memory must be an object")
    allocated = _positive(memory.get("peak_allocated_bytes"), "peak_allocated_bytes", integer=True)
    reserved = _positive(memory.get("peak_reserved_bytes"), "peak_reserved_bytes", integer=True)
    margin = _positive(memory.get("configured_margin_bytes"), "configured_margin_bytes", integer=True, allow_zero=True)
    budget = _positive(memory.get("admission_budget_bytes"), "admission_budget_bytes", integer=True)
    if not (allocated <= reserved and budget == reserved + margin and allocated >= 16 * 124439808):
        raise ValueError("A1 memory values are inconsistent with measured storage and explicit policy margin")
    measured.update(peak_memory_gib=budget / 2**30, local_batch_size=batch, sequence_length=sequence)
    return measured


def import_profile(profile_path: Path, output: Path) -> dict:
    """Validate A1 raw evidence and rerun compact/competition CPU simulations.

    Only forward/backward, optimizer and reserved-memory-plus-margin inputs are
    reused. Neither recovery costs nor cross-server bandwidth are calibrated.
    """
    _domain_paths()
    from fabric_sim import simulate
    from fabric_sim.ddp import demo_ddp, training_profile
    profile_path = Path(profile_path).expanduser().resolve()
    if profile_path.stat().st_size > 16 * 1024**2:
        raise ValueError("A1 evidence file exceeds 16 MiB")
    raw = profile_path.read_bytes()
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("A1 evidence must be a JSON object")
    measured = _validated_a1(data)
    digest = hashlib.sha256(raw).hexdigest()
    timing_profile = {"source": "MEASURED", "measurement_id": f"sha256:{digest}", **measured}
    normalized = training_profile(timing_profile)
    output = _output(output)
    (output / "source_profile.json").write_bytes(raw)
    records = []
    for variant in ("compact", "competition"):
        spec = demo_ddp(variant)
        spec["profile"] = deepcopy(timing_profile)
        spec["horizon_ms"] = 60000
        result = simulate(spec)
        atomic_json(output / f"ddp_{variant}_input.json", spec)
        atomic_json(output / f"ddp_{variant}_result.json", result)
        records.append({"variant": variant, "input_hash": result["input_hash"],
                        "all_invariants_passed": all(c["passed"] for c in result["invariant_checks"]),
                        "all_jobs_completed": all(j["state"] == "completed" for j in result["jobs"]),
                        "summary": result["summary"], "engine_wall_ms": result["engine_wall_ms"],
                        "input": str(output / f"ddp_{variant}_input.json"),
                        "result": str(output / f"ddp_{variant}_result.json")})
    report = {
        "backend": "simulation", "status": "completed", "source_measurement_sha256": digest,
        "source_measurement_path": str(profile_path), "retained_source": str(output / "source_profile.json"),
        "profile": normalized, "records": records,
        "checks": {"a1_contract_validated": True,
                   "recovery_inputs_still_synthetic": all(normalized["field_sources"][field] == "SYNTHETIC"
                       for field in ("checkpoint_save_ms", "restore_ms", "group_init_ms")),
                   "all_invariants_passed": all(r["all_invariants_passed"] for r in records)},
        "memory_provenance": {"measured_peak_reserved_bytes": data["memory"]["peak_reserved_bytes"],
                              "policy_margin_bytes": data["memory"]["configured_margin_bytes"],
                              "note": "Admission budget is measured reserved memory plus an explicit unmeasured policy margin; DDP buckets/NCCL overhead were not profiled."},
        "limitations": [
            "A1 measured a single-GPU GPT-2 workload, not actual DDP iteration time.",
            "A2/A5 standalone NCCL data must not be substituted for cross-server NIC/uplink bandwidth.",
            "Simulation has no real GPU process or failure execution; live DDP is a separate backend.",
            "Recovery timing and all network capacities remain synthetic assumptions.",
            "Both layouts use the same model, workload, profile, topology and 60000 ms horizon; only placement differs.",
            "Unfinished jobs at the horizon are reported; simulation completion is not job completion.",
        ],
        "artifacts": {"report": str(output / "report.json")},
    }
    atomic_json(output / "report.json", report)
    return report
