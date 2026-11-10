"""Local presentation adapter around the unchanged FabricSandbox v2 engine.

The planner below is new demonstration glue: it compares a finite, declared
placement set with the original simulator. It is not a GPU/LLM execution path.
Python 3.10+, standard library only. No network access or file writes on calls.
"""
from __future__ import annotations

from copy import deepcopy
import math
from pathlib import Path
import re
import sys

VENDOR = Path(__file__).resolve().parent / "vendor"
if str(VENDOR) not in sys.path:
    sys.path.insert(0, str(VENDOR))
from fabric_sim import canonical_hash, implementation_hash, simulate, validate_spec
from fabric_sim.ddp import demo_ddp, _Training
from fabric_sim.tp import demo_tp

MODEL_HASH = implementation_hash()
PROVENANCE = {
    "mode": "LOCAL_SIMULATION",
    "measurement_status": "SIMULATION_NOT_GPU_MEASUREMENT",
    "profile_source": "SYNTHETIC",
    "kernel": "FabricSandbox v2 / fabric_sim 0.2.0",
    "implementation_hash": MODEL_HASH,
    "source": "vendor/fabric_sim (unmodified copy)",
    "planner_source": "demo_model.py / finite candidate comparison",
    "boundary": "本机实时重算仿真；不调用真实GPU，不使用LLM，不宣称全局最优。",
}
def _number(value, name, low, high, integer=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name}必须为数值")
    try:
        finite = math.isfinite(value)
    except (OverflowError, ValueError):
        finite = False
    if not finite or not low <= value <= high:
        raise ValueError(f"{name}必须在{low}到{high}之间")
    if integer and not isinstance(value, int):
        raise ValueError(f"{name}必须为整数")
    return value


def validate_topology(payload):
    """Validate a topology object or {topology: object}, without running jobs.

    Defaults and bounds match the unchanged v2 kernel. Unknown fields are
    rejected so a misspelled capacity cannot silently use a default instead.
    """
    if not isinstance(payload, dict):
        raise ValueError("拓扑必须为JSON对象")
    canonical_hash(payload)
    if "topology" in payload:
        if set(payload) != {"topology"}:
            raise ValueError("拓扑校验包装对象只允许topology字段")
        payload = payload["topology"]
    if not isinstance(payload, dict):
        raise ValueError("topology必须为JSON对象")
    defaults = {"node_bandwidth_gbps": 100, "uplink_bandwidth_gbps": 50,
                "local_bandwidth_gbps": 200, "link_latency_us": 5,
                "local_latency_us": 2}
    unknown = set(payload) - {"nodes", *defaults}
    if unknown:
        raise ValueError("未知拓扑字段：" + ", ".join(sorted(unknown)))
    nodes = payload.get("nodes")
    if not isinstance(nodes, list) or not 1 <= len(nodes) <= 16:
        raise ValueError("topology.nodes必须包含1到16个节点")
    normalized, seen = [], set()
    for node in nodes:
        if not isinstance(node, dict):
            raise ValueError("每个拓扑节点必须为对象")
        unknown = set(node) - {"id", "rack_id", "gpu_count", "memory_gib"}
        if unknown:
            raise ValueError("未知节点字段：" + ", ".join(sorted(unknown)))
        for key in ("id", "rack_id"):
            if not isinstance(node.get(key), str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,48}", node[key]):
                raise ValueError(f"node.{key}必须为1到48位英文字母、数字、下划线或连字符")
        if node["id"] in seen:
            raise ValueError("节点ID重复：" + node["id"])
        seen.add(node["id"])
        normalized.append({"id": node["id"], "rack_id": node["rack_id"],
            "gpu_count": _number(node.get("gpu_count", 2), "gpu_count", 1, 8, True),
            "memory_gib": _number(node.get("memory_gib", 24), "memory_gib", .01, 1024)})
    total = sum(n["gpu_count"] for n in normalized)
    if total > 64:
        raise ValueError("拓扑最多支持64张GPU")
    top = {"nodes": sorted(normalized, key=lambda n: n["id"])}
    for key, default in defaults.items():
        is_latency = key.endswith("latency_us")
        top[key] = _number(payload.get(key, default), key,
                           0 if is_latency else .001,
                           1000000 if is_latency else 100000)
    return {"schema_version": "fabric-demo-topology/v1", "topology": top,
            "topology_hash": canonical_hash(top),
            "summary": {"racks": len({n["rack_id"] for n in normalized}),
                        "nodes": len(normalized), "gpus": total},
            "planning_min_gpus": 8,
            "boundary": "节点—机架二层有效拓扑；不支持任意交换机边列表或真实GPU调度。"}


def default_topology():
    """The original two-rack topology, suitable for the JSON editor."""
    return validate_topology(demo_ddp("competition")["topology"])


def _custom_options(payload):
    if not isinstance(payload, dict):
        raise ValueError("输入必须为JSON对象")
    canonical_hash(payload)
    if "uplink_gbps" in payload or "memory_gib" in payload:
        raise ValueError("自定义拓扑请在topology中设置带宽和显存，不要同时传uplink_gbps或memory_gib")
    unknown = set(payload) - {"topology", "blocked_nodes", "steps", "include_raw", "scenario", "variant"}
    if unknown:
        raise ValueError("未知自定义仿真字段：" + ", ".join(sorted(unknown)))
    if payload.get("scenario", "ddp") != "ddp":
        raise ValueError("自定义拓扑入口当前支持双DDP作业；原TP入口保持可用")
    checked = validate_topology({"topology": payload["topology"]})
    if checked["summary"]["gpus"] < 8:
        raise ValueError("当前演示固定两个4卡DDP作业，自定义拓扑至少需要8张物理GPU")
    blocked = payload.get("blocked_nodes", [])
    ids = {n["id"] for n in checked["topology"]["nodes"]}
    if not isinstance(blocked, list) or any(not isinstance(x, str) or x not in ids for x in blocked):
        raise ValueError("blocked_nodes必须是当前拓扑中节点ID组成的数组")
    if not isinstance(payload.get("include_raw", False), bool):
        raise ValueError("include_raw必须为布尔值")
    return {"topology": checked["topology"], "blocked_nodes": sorted(set(blocked)),
            "steps": _number(payload.get("steps", 30), "steps", 1, 30, True),
            "include_raw": payload.get("include_raw", False)}


def _custom_placements(options):
    """Three bounded, deterministic policies; never enumerate GPU combinations."""
    nodes = options["topology"]["nodes"]
    by_rack = {}
    for node in sorted(nodes, key=lambda n: (n["rack_id"], n["id"])):
        by_rack.setdefault(node["rack_id"], []).append(node)
    # Interleave racks, then nodes, then GPU indices for the initial plan.
    interleaved = [rack[i] for i in range(max(map(len, by_rack.values())))
                   for rack in by_rack.values() if i < len(rack)]
    spread = [f"{n['id']}:gpu{i}" for i in range(max(n["gpu_count"] for n in nodes))
              for n in interleaved if i < n["gpu_count"]][:8]
    # Prefer usable GPUs, then keep each complete group close by rack/node.
    blocked = set(options["blocked_nodes"])
    compact = [f"{n['id']}:gpu{i}" for n in sorted(nodes, key=lambda n:
               (n["id"] in blocked or n["memory_gib"] < 4, n["rack_id"], n["id"]))
               for i in range(n["gpu_count"])][:8]
    return {"distributed": {"A": spread[:4], "B": spread[4:]},
            "compact": {"A": compact[:4], "B": compact[4:]},
            "compact_reverse": {"A": compact[4:], "B": compact[:4]}}


def _custom_spec(options, variant):
    placements = _custom_placements(options)
    if variant not in {*placements, "baseline"}:
        raise ValueError("自定义拓扑variant仅支持distributed、compact、compact_reverse或baseline")
    spec = demo_ddp("competition")
    spec["topology"] = deepcopy(options["topology"])
    allocation = placements["distributed" if variant == "baseline" else variant]
    for job in spec["jobs"]:
        job["gpu_ids"] = allocation[job["id"]]
        job["steps"] = options["steps"]
    if variant == "baseline":
        spec["jobs"] = spec["jobs"][:1]
    spec["horizon_ms"] = 60000
    spec["demo_variant"] = variant
    spec["faults"] = [{"at_ms": 0, "type": "node_down", "target_id": node}
                      for node in options["blocked_nodes"]]
    return validate_spec(spec)


def _options(payload):
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        raise ValueError("输入必须为JSON对象")
    canonical_hash(payload)  # rejects non-finite/non-JSON input
    blocked = payload.get("blocked_nodes", [])
    if not isinstance(blocked, list) or any(not isinstance(x, str) or x not in {"n0", "n1", "n2", "n3"} for x in blocked):
        raise ValueError("blocked_nodes必须是n0–n3组成的数组")
    if not isinstance(payload.get("include_raw", False), bool):
        raise ValueError("include_raw必须为布尔值")
    return {
        "uplink_gbps": _number(payload.get("uplink_gbps", 50), "uplink_gbps", 5, 200),
        "memory_gib": _number(payload.get("memory_gib", 24), "memory_gib", .1, 128),
        "blocked_nodes": sorted(set(blocked)),
        "steps": _number(payload.get("steps", 30), "steps", 1, 30, True),
        "include_raw": payload.get("include_raw", False),
    }


def _configure(spec, options, payload):
    spec = deepcopy(spec)
    spec["topology"]["uplink_bandwidth_gbps"] = options["uplink_gbps"]
    # Do not erase an intrinsic memory-rejection variant unless explicitly changed.
    if "memory_gib" in payload:
        for node in spec["topology"]["nodes"]:
            node["memory_gib"] = options["memory_gib"]
    if spec["scenario"] == "ddp":
        for job in spec["jobs"]:
            job["steps"] = options["steps"]
        spec["horizon_ms"] = 60000  # bounded observation includes slow 5Gbps cases
    if options["blocked_nodes"]:
        # A snapshot failure is injected before admission. No hardware action.
        spec["faults"] = [{"at_ms": 0, "type": "node_down", "target_id": node}
                          for node in options["blocked_nodes"]] + spec.get("faults", [])
    return spec


def _workload_hash(spec):
    """Same-workload guard: exclude only placement and demo display labels."""
    value = deepcopy(spec)
    value.pop("placement_policy", None)
    value.pop("demo_variant", None)
    for job in value.get("jobs", []):
        job.pop("gpu_ids", None)
    return canonical_hash(value)


def _metrics(result):
    summary = result["summary"]
    completed = [j for j in result.get("jobs", []) if j["state"] == "completed"]
    all_completed = bool(result.get("jobs")) and len(completed) == len(result["jobs"])
    rack_samples = [x for x in result["resource_samples"] if x["resource_id"].startswith("rack:")]
    cross = sum(e["size_bytes"] for e in result["events"]
                if e["kind"] == "transfer_completed" and any(r.startswith("rack:") for r in e["resource_ids"]))
    return {
        "makespan_ms": (max(j["completed_at_ms"] for j in completed) if all_completed else None)
            if result["scenario"] == "ddp" else summary.get("simulated_end_ms"),
        "mean_completion_ms": summary.get("mean_completion_ms"),
        "completed_jobs": summary.get("completed_jobs"),
        "all_jobs_completed": all_completed,
        "completed_requests": summary.get("completed"),
        "failed_requests": summary.get("failed"),
        "mean_ttft_ms": summary.get("mean_ttft_ms"),
        "uplink_peak_gbps": max((x["allocated_gbps"] for x in rack_samples), default=0),
        "cross_rack_completed_bytes": cross,
        "total_completed_transfer_bytes": result["engine_stats"]["completed_transfer_bytes"],
        "max_capacity_ratio": result["engine_stats"]["max_capacity_ratio"],
        "lost_steps": summary.get("lost_steps", 0),
        "restarts": summary.get("restarts", 0),
    }


def _representative_samples(samples):
    by_resource = {}
    for x in samples:
        by_resource.setdefault(x["resource_id"], []).append(x)
    kept = []
    for rows in by_resource.values():
        indices = {0, len(rows)-1}
        if len(rows) <= 64:
            indices.update(range(len(rows)))
        else:
            indices.update(round(i*(len(rows)-1)/63) for i in range(64))
        indices.add(max(range(len(rows)), key=lambda i: rows[i]["allocated_gbps"]))
        indices.add(max(range(len(rows)), key=lambda i: rows[i]["active_flows"]))
        kept.extend(rows[i] for i in sorted(indices))
    return sorted(kept, key=lambda x: (x["time_ms"], x["resource_id"]))


def _pack(result, include_raw=False):
    packed = deepcopy({k: v for k, v in result.items() if k not in {"events", "resource_samples"}})
    packed["metrics"] = _metrics(result)
    packed["raw_counts"] = {"events": len(result["events"]), "resource_samples": len(result["resource_samples"])}
    if include_raw:
        packed["events"] = deepcopy(result["events"])
        packed["resource_samples"] = deepcopy(result["resource_samples"])
    else:
        # All non-communication business events are retained. Add one actual
        # initial-step transfer sample for the topology animation/evidence panel.
        packed["events"] = [deepcopy(e) for e in result["events"]
                            if not e["kind"].startswith(("transfer_", "collective_", "allreduce_"))]
        packed["representative_transfers"] = [deepcopy(e) for e in result["events"]
                                               if e["kind"] in {"transfer_started", "transfer_completed"}][:96]
        packed["resource_samples"] = _representative_samples(result["resource_samples"])
    packed["display_counts"] = {"events": len(packed["events"]), "resource_samples": len(packed["resource_samples"])}
    packed["display_sampling"] = {
        "raw_included": include_raw,
        "events": "完整事件" if include_raw else "保留全部业务事件；传输和集合通信细节单独抽取代表记录",
        "resource_samples": "完整容量样本" if include_raw else "按资源保留首尾、均匀时点与峰值，共至多66点/资源",
        "note": "采样只用于展示，所有指标和断言由完整原始仿真结果计算。",
    }
    return packed


def simulate_case(payload=None):
    """Run the unmodified simulator on a bounded demo variant, then expose evidence."""
    payload = {} if payload is None else payload
    if isinstance(payload, dict) and "topology" in payload:
        options = _custom_options(payload)
        variant = payload.get("variant", "distributed")
        if not isinstance(variant, str):
            raise ValueError("variant必须为字符串")
        return _simulate_spec(_custom_spec(options, variant), variant, options["include_raw"])
    options = _options(payload)
    scenario = payload.get("scenario", "ddp")
    variant = payload.get("variant", "distributed" if scenario == "ddp" else "fault")
    if not isinstance(variant, str):
        raise ValueError("variant必须为字符串")
    if scenario == "ddp":
        aliases = {"distributed": "competition", "baseline": "competition", "compact_reverse": "compact"}
        spec = demo_ddp(aliases.get(variant, variant))
        if variant == "baseline":
            spec["jobs"] = spec["jobs"][:1]
        elif variant == "compact_reverse":
            spec["jobs"][0]["gpu_ids"], spec["jobs"][1]["gpu_ids"] = spec["jobs"][1]["gpu_ids"], spec["jobs"][0]["gpu_ids"]
    elif scenario == "tp":
        spec = demo_tp(variant)
    else:
        raise ValueError("scenario必须为ddp或tp")
    spec = _configure(spec, options, payload)
    if scenario == "ddp" and variant == "checkpoint":
        healthy = simulate(spec | {"faults": []})
        checkpoint = next((t for t in healthy["jobs"][0]["state_timeline"] if t["state"] == "checkpointing"), None)
        if checkpoint is None:
            raise ValueError("当前资源/步数无法完成首次检查点，不能设置保存中断场景")
        for fault in spec["faults"]:
            if fault["target_id"] == "n0" and fault["at_ms"] > 0:
                fault["at_ms"] = (checkpoint["start_ms"]+checkpoint["end_ms"])/2
    spec = validate_spec(spec)
    return _simulate_spec(spec, variant, options["include_raw"])


def _simulate_spec(spec, variant, include_raw):
    scenario = spec["scenario"]
    raw = simulate(spec)
    result = _pack(raw, include_raw)
    placements = {j["id"]: list(j.get("gpu_ids", [])) for j in spec.get("jobs", [])}
    if scenario == "tp":
        placements = {j["id"]: list(j["gpu_ids"]) for j in spec["replicas"]}
    return {
        "schema_version": "fabric-demo-simulation/v1", "scenario": scenario, "variant": variant,
        "input_hash": raw["input_hash"], "workload_hash": _workload_hash(spec),
        "spec": spec, "topology": spec["topology"],
        "jobs": spec.get("jobs", []), "placements": placements,
        "provenance": deepcopy(PROVENANCE), "result": result,
        "summary": result["summary"], "metrics": result["metrics"],
        "events": result["events"], "resource_samples": result["resource_samples"],
        "raw_counts": result["raw_counts"], "display_sampling": result["display_sampling"],
    }


def _candidate_check(spec, placements, blocked_nodes):
    """Check a complete simultaneous plan using the original domain requirements.

    This is explicit preflight glue; memory and Ring reachability use original
    training_profile and _Training.connected. It is not a new scheduling kernel.
    """
    training = _Training(validate_spec(spec))
    for node in blocked_nodes:
        training.network.fail_node(node)
    checks, reasons, used = [], [], {}
    for job in spec["jobs"]:
        jid, world = job["id"], job["world_size"]
        allocation = placements[jid]
        group_ok = len(allocation) == world and len(set(allocation)) == world
        checks.append({"code": "complete_group", "job_id": jid, "passed": group_ok, "required": world, "provided": len(allocation)})
        if not group_ok:
            reasons.append(f"{jid}需要完整{world}卡通信组，当前分配无效")
        low = [g for g in allocation if training.network.gpus[g].memory_gib < training.profile["peak_memory_gib"]]
        checks.append({"code": "memory", "job_id": jid, "passed": not low, "required_gib": training.profile["peak_memory_gib"], "gpu_ids": low})
        if low:
            reasons.append(f"{jid}单卡显存不足：需要{training.profile['peak_memory_gib']:g}GiB，指定卡只有{training.network.gpus[low[0]].memory_gib:g}GiB")
        unavailable = [g for g in allocation if training.network.gpus[g].node_id in training.network.down_nodes]
        checks.append({"code": "healthy_nodes", "job_id": jid, "passed": not unavailable, "gpu_ids": unavailable})
        if unavailable:
            reasons.append(f"{jid}包含失效节点上的GPU：{', '.join(unavailable)}")
        connected = training.connected(tuple(allocation))
        checks.append({"code": "ring_connected", "job_id": jid, "passed": connected})
        if not connected:
            reasons.append(f"{jid}完整Ring通信路径不可达")
        conflicts = [g for g in allocation if g in used]
        checks.append({"code": "exclusive_ownership", "job_id": jid, "passed": not conflicts, "gpu_ids": conflicts})
        if conflicts:
            reasons.append(f"{jid}与其他作业重复占用：{', '.join(conflicts)}")
        used.update({g: jid for g in allocation})
    healthy = len(training.network.available_gpu_ids())
    required = sum(j["world_size"] for j in spec["jobs"])
    checks.append({"code": "simultaneous_group_capacity", "passed": healthy >= required, "healthy_gpus": healthy, "required_gpus": required})
    if healthy < required:
        reasons.append(f"两作业同时需要{required}卡，仅{healthy}张健康卡；不能拆小固定通信组")
    return checks, reasons


def plan_case(payload=None):
    """Enumerate nine declared placements and evaluate all feasible full plans.

    Each job chooses spread, rack0 or rack1. Shared GPUs reject a combination.
    This is a finite demo policy, not an exhaustive/global optimizer. A-alone
    baseline is a contention control and is not eligible as a two-job plan.
    """
    payload = {} if payload is None else payload
    if isinstance(payload, dict) and "topology" in payload:
        return _plan_custom(payload)
    options = _options(payload)
    spec = _configure(demo_ddp("competition"), options, payload)
    input_hash = canonical_hash(validate_spec(spec))
    spread = {"A": [f"n{i}:gpu0" for i in range(4)], "B": [f"n{i}:gpu1" for i in range(4)]}
    rack0 = ["n0:gpu0", "n0:gpu1", "n1:gpu0", "n1:gpu1"]
    rack1 = ["n2:gpu0", "n2:gpu1", "n3:gpu0", "n3:gpu1"]
    combos = [("spread", "spread"), ("r0", "r1"), ("r1", "r0"),
              ("spread", "r0"), ("spread", "r1"), ("r0", "spread"), ("r1", "spread"), ("r0", "r0"), ("r1", "r1")]
    names = {("spread", "spread"): ("distributed", "当前分散放置"), ("r0", "r1"): ("compact", "A在机架r0，B在机架r1"), ("r1", "r0"): ("compact_reverse", "A在机架r1，B在机架r0")}
    candidates, evaluated = [], {}
    for a, b in combos:
        variant, label = names.get((a, b), (f"{a}_{b}", f"A={a}，B={b}"))
        placement = {"A": spread["A"] if a == "spread" else rack0 if a == "r0" else rack1,
                     "B": spread["B"] if b == "spread" else rack0 if b == "r0" else rack1}
        checks, reasons = _candidate_check(spec, placement, options["blocked_nodes"])
        moved = sum(len(set(placement[j]) - set(spread[j])) for j in placement)
        item = {"id": variant, "variant": variant, "label": label, "placements": deepcopy(placement),
                "admissible": not reasons, "accepted": not reasons, "reasons": reasons,
                "rejection_reasons": reasons, "checks": checks, "moved_gpu_assignments": moved,
                "metrics": None, "input_hash": None, "workload_hash": None}
        if not reasons:
            run = simulate_case({**options, "variant": variant, "scenario": "ddp"})
            evaluated[variant] = run
            item["metrics"] = run["metrics"]
            item["input_hash"] = run["input_hash"]
            item["workload_hash"] = run["workload_hash"]
            item["invariant_checks"] = run["result"]["invariant_checks"]
        candidates.append(item)
    feasible = [c for c in candidates if c["admissible"] and c["metrics"]["all_jobs_completed"]]
    selected = min(feasible, key=lambda c: (c["metrics"]["makespan_ms"], c["moved_gpu_assignments"], c["id"])) if feasible else None
    baseline = simulate_case({**options, "scenario": "ddp", "variant": "baseline"})
    initial = evaluated.get("distributed") or simulate_case({**options, "scenario": "ddp", "variant": "distributed"})
    def comparison(run):
        return {"variant": run["variant"], "input_hash": run["input_hash"],
                "workload_hash": run["workload_hash"], "summary": run["summary"],
                "metrics": run["metrics"], "placements": run["placements"]}
    comparisons = {"baseline": comparison(baseline)}
    comparisons.update({k: comparison(v) for k, v in evaluated.items()})
    shared = next((x for x in initial["result"]["resource_samples"]
                   if x["resource_id"] == "rack:r0:tx" and x["active_flows"] >= 2 and x["allocated_gbps"] > 0), None)
    speedup = (initial["metrics"]["makespan_ms"] / selected["metrics"]["makespan_ms"]
               if selected and initial["metrics"]["all_jobs_completed"] else None)
    return {
        "schema_version": "fabric-demo-plan/v1", "status": "planned" if selected else "blocked",
        "input_hash": input_hash, "workload_hash": _workload_hash(validate_spec(spec)),
        "request": options, "spec": spec, "topology": spec["topology"],
        "jobs": spec["jobs"], "placements": spread, "provenance": deepcopy(PROVENANCE),
        "candidate_policy": "每作业选择spread/r0/r1，枚举9种组合；排除显存、健康、独占与完整Ring约束违反项",
        "selection_rule": "minimum_simulated_makespan_then_moved_assignments_then_id",
        "selected_candidate_id": selected["id"] if selected else None,
        "selected_variant": selected["variant"] if selected else None,
        "selected_candidate": selected, "candidates": candidates, "comparison": comparisons,
        "comparison_contract": {
            "same_workload_variants": [c["variant"] for c in feasible],
            "same_workload_hash": len({c["workload_hash"] for c in feasible}) <= 1,
            "held_constant": ["topology", "jobs except gpu_ids", "profile", "faults", "horizon_ms"],
            "changed": ["jobs.gpu_ids", "placement_policy", "demo_variant"],
            "baseline_role": "A alone is a contention control; not a two-job scheduling candidate",
        },
        "predicted_speedup": speedup,
        "diagnosis": {"code": "shared_uplink_contention" if shared else "resource_constraints" if not selected else "no_shared_uplink_sample",
                      "evidence": shared, "source": "result.resource_samples",
                      "explanation": "不同GPU上的作业仍共用机架方向容量；候选评估保持相同负载。" if shared else "检查全部候选的原始约束与拒绝理由。"},
        "result": initial["result"], "summary": initial["summary"], "events": initial["events"],
        "resource_samples": initial["resource_samples"], "raw_counts": initial["raw_counts"],
        "boundary": "baseline仅含A，用来隔离竞争；distributed/compact均含相同A+B。推荐仅适用于已枚举候选和当前合成参数。",
    }


def _plan_custom(payload):
    options = _custom_options(payload)
    placements = _custom_placements(options)
    spec = _custom_spec(options, "distributed")
    labels = {"distributed": "当前跨节点轮转放置", "compact": "按机架与节点集中放置",
              "compact_reverse": "集中放置并交换A/B分配"}
    candidates, evaluated = [], {}
    for variant, allocation in placements.items():
        checks, reasons = _candidate_check(spec, allocation, options["blocked_nodes"])
        moved = sum(len(set(allocation[j]) - set(placements["distributed"][j])) for j in allocation)
        item = {"id": variant, "variant": variant, "label": labels[variant],
                "placements": deepcopy(allocation), "admissible": not reasons,
                "accepted": not reasons, "reasons": reasons, "rejection_reasons": reasons,
                "checks": checks, "moved_gpu_assignments": moved,
                "metrics": None, "input_hash": None, "workload_hash": None}
        if not reasons:
            run = simulate_case({**options, "variant": variant})
            evaluated[variant] = run
            item.update(metrics=run["metrics"], input_hash=run["input_hash"],
                        workload_hash=run["workload_hash"],
                        invariant_checks=run["result"]["invariant_checks"])
        candidates.append(item)
    feasible = [c for c in candidates if c["admissible"] and c["metrics"]["all_jobs_completed"]]
    selected = min(feasible, key=lambda c: (c["metrics"]["makespan_ms"],
                    c["moved_gpu_assignments"], c["id"])) if feasible else None
    baseline = simulate_case({**options, "variant": "baseline"})
    initial = evaluated.get("distributed") or simulate_case({**options, "variant": "distributed"})
    def comparison(run):
        return {"variant": run["variant"], "input_hash": run["input_hash"],
                "workload_hash": run["workload_hash"], "summary": run["summary"],
                "metrics": run["metrics"], "placements": run["placements"]}
    comparisons = {"baseline": comparison(baseline)}
    comparisons.update({k: comparison(v) for k, v in evaluated.items()})
    shared = next((x for x in initial["resource_samples"]
                   if x["resource_id"].startswith("rack:") and
                   x["active_flows"] >= 2 and x["allocated_gbps"] > 0), None)
    return {"schema_version": "fabric-demo-plan/v1", "status": "planned" if selected else "blocked",
        "input_hash": initial["input_hash"], "workload_hash": initial["workload_hash"],
        "request": options, "spec": spec, "topology": spec["topology"],
        "topology_hash": canonical_hash(spec["topology"]), "topology_source": "custom_json",
        "jobs": spec["jobs"], "placements": deepcopy(placements["distributed"]),
        "provenance": deepcopy(PROVENANCE),
        "candidate_policy": "固定3种候选：跨机架/节点轮转、按机架节点集中、集中后交换A/B；集中候选优先健康且显存合格的GPU，不进行全局搜索",
        "selection_rule": "minimum_simulated_makespan_then_moved_assignments_then_id",
        "selected_candidate_id": selected["id"] if selected else None,
        "selected_variant": selected["variant"] if selected else None,
        "selected_candidate": selected, "candidates": candidates, "comparison": comparisons,
        "comparison_contract": {
            "same_workload_variants": [c["variant"] for c in feasible],
            "same_workload_hash": len({c["workload_hash"] for c in feasible}) <= 1,
            "held_constant": ["topology", "jobs except gpu_ids", "profile", "faults", "horizon_ms"],
            "changed": ["jobs.gpu_ids", "placement_policy", "demo_variant"],
            "baseline_role": "A alone is a contention control; not a two-job scheduling candidate"},
        "predicted_speedup": (initial["metrics"]["makespan_ms"] / selected["metrics"]["makespan_ms"]
                              if selected and initial["metrics"]["all_jobs_completed"] else None),
        "diagnosis": {"code": "shared_uplink_contention" if shared else "resource_constraints" if not selected else "no_shared_uplink_sample",
            "evidence": shared, "source": "result.resource_samples",
            "explanation": "不同GPU上的作业仍共用机架方向容量；候选评估保持相同负载。" if shared else
                           "检查候选约束与完成状态；未观测到共享机架上联容量样本。"},
        "result": initial["result"], "summary": initial["summary"], "events": initial["events"],
        "resource_samples": initial["resource_samples"], "raw_counts": initial["raw_counts"],
        "boundary": "自定义节点—机架拓扑；固定两个4卡DDP作业及合成profile；baseline只运行A；推荐限于3种已声明候选。"}


if __name__ == "__main__":
    import json
    plan = plan_case()
    print(json.dumps({"status": plan["status"], "selected": plan["selected_variant"],
                      "comparison": plan["comparison"], "provenance": plan["provenance"]}, ensure_ascii=False, indent=2))
