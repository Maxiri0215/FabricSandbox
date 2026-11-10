from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import sys
import threading
import time

from .common import atomic_json, require_new_output
from .controller import Controller
from .reporting import write_report, write_suite_index


def check(name, passed, detail=""):
    return {"name": name, "passed": bool(passed), "detail": detail}


def persist(result, output):
    atomic_json(output / "result.json", result)
    write_report(result, output / "report.html")
    print(json.dumps({"status": result.get("status"), "checks_passed": all(x["passed"] for x in result.get("checks", [])),
                      "result": str(output / "result.json"), "report": str(output / "report.html")}, ensure_ascii=False), flush=True)
    return result


def monitor_with_injector(adapter, output, mode, timeout, inject):
    """Experiment-only driver. Controller receives no injection target or truth."""
    ready = threading.Event()
    cancel = threading.Event()
    outcome = {}
    def invoke():
        if not ready.wait(timeout=min(timeout, 30)):
            outcome["error"] = "Controller did not begin monitoring"
            return
        time.sleep(.25)  # Obtain independent healthy observations before injection.
        if cancel.is_set():
            outcome["error"] = "Experiment cancelled before fault injection"
            return
        try:
            outcome["truth"] = inject(cancel)
        except Exception as exc:
            outcome["error"] = f"{type(exc).__name__}: {exc}"
    injector = threading.Thread(target=invoke, name="experiment-injector", daemon=True)
    injector.start()
    incident = Controller(adapter, output / "agent", mode=mode).run(watch_timeout=timeout, ready=ready)
    cancel.set()
    injector.join(timeout=3)
    if injector.is_alive():
        outcome["error"] = "Injector did not finish before the controller terminated"
    if "error" in outcome:
        # Driver failure is a failed experiment, even if an unrelated fault was handled.
        incident["experiment_driver_error"] = outcome["error"]
    return incident, outcome


def run_tcp(args, output):
    from .tcp_backend import TCPAdapter
    adapter = TCPAdapter(output / "backend", timeout=args.timeout, initial_channel=args.initial_channel)
    result = {"schema_version": 1, "experiment": "tcp:" + args.scenario, "backend": "live_tcp",
              "source": "MEASURED", "boundary": "真实回环 TCP 传输与应用层通道切换；不代表物理交换机、RDMA 或 GPU 网络实测。", "checks": []}
    try:
        result["snapshot"] = adapter.start()
        result["baseline"] = adapter.baseline()
        result["checks"].append(check("healthy_baseline_payload_verified", result["baseline"]["recovered"]))
        if not result["baseline"]["recovered"]:
            raise RuntimeError("Healthy baseline failed; do not interpret subsequent fault experiments")
        if args.scenario == "baseline":
            result.update(status="healthy", verification=result["baseline"])
        else:
            kind = ("all_channels" if args.scenario == "blocked" else "receiver" if args.scenario == "receiver-down"
                    else "channel_a" if args.initial_channel == "A" else "channel_b")
            # Truth is kept by the experiment driver; never given to Controller.
            result["ground_truth_file"] = "backend/ground_truth.json"
            if args.scenario == "no-repair":
                adapter.inject(kind)
                observation = adapter.observe()
                result.update(status="unrecovered_control", observation=observation)
                result["checks"].append(check("fault_has_observable_effect", observation["status"] != "healthy"))
            else:
                incident, injection = monitor_with_injector(adapter, output, args.mode, 30,
                                                             lambda cancel: adapter.inject(kind))
                truth = injection.get("truth", {})
                result["incident"] = incident
                result["status"] = incident["status"]
                expected = {"recover": "recovered", "blocked": "blocked", "receiver-down": "inconclusive"}[args.scenario]
                result["checks"].append(check("expected_terminal_state", incident["status"] == expected,
                                              f"expected={expected}, actual={incident['status']}"))
                result["checks"].append(check("independent_injection_completed", "truth" in injection and "error" not in injection))
                if incident.get("completed_wall_time") and truth.get("injected_at"):
                    result["fault_to_terminal_ms"] = (incident["completed_wall_time"] - truth["injected_at"]) * 1000
                    result["detection_delay_ms"] = (incident["detected_wall_time"] - truth["injected_at"]) * 1000
                if args.scenario == "recover":
                    result["checks"].append(check("actual_channel_changed", adapter.active != args.initial_channel))
    except Exception as exc:
        result.update(status="failed", error_type=type(exc).__name__, error=str(exc), source="ATTEMPTED")
        result["checks"].append(check("execution_completed", False, str(exc)))
    finally:
        try:
            adapter.close()
        except Exception as exc:
            result.update(status="failed", cleanup_error=f"{type(exc).__name__}: {exc}")
            result["checks"].append(check("owned_process_cleanup", False, str(exc)))
    return persist(result, output)


def run_ddp(args, output):
    from .ddp_backend import DDPAdapter
    allowed = [x.strip() for x in args.gpus.split(",") if x.strip()]
    pool = allowed[:2] if args.scenario == "blocked" else allowed
    adapter = DDPAdapter(output / "backend", pool, device=args.device, model=args.model,
                         steps=args.steps, checkpoint_every=args.checkpoint_every,
                         sequence_length=args.sequence_length, timeout=args.timeout)
    result = {"schema_version": 1, "experiment": "ddp:" + args.scenario, "backend": adapter.backend,
              "source": "MEASURED", "allowed_pool": pool, "checks": [],
              "boundary": "真实单机 DDP worker 故障、完整组重建与检查点恢复；执行槽隔离是策略，不等于 GPU 硬件故障诊断。"}
    try:
        if args.scenario == "baseline":
            result["snapshot"] = adapter.start()
            verification = adapter.wait_completed()
            result["verification"] = verification
            completed = verification.get("recovered", verification.get("completed", verification.get("status") == "completed"))
            result["status"] = "healthy" if completed else "failed"
            result["checks"].append(check("healthy_training_completed", completed))
        else:
            result["snapshot"] = adapter.start(fault_at_step=args.fault_step)
            result["ground_truth_file"] = "backend/ground_truth.json"
            if args.scenario == "no-repair":
                adapter.inject(at_step=args.fault_step)
                observation = adapter.observe()
                result.update(status="unrecovered_control", observation=observation)
                result["checks"].append(check("fault_has_observable_effect", observation["status"] not in {"healthy", "completed"}))
            else:
                incident, injection = monitor_with_injector(adapter, output, args.mode, args.timeout,
                    lambda cancel: adapter.inject(at_step=args.fault_step, wait_observed=False, cancel_event=cancel))
                truth = injection.get("truth", {})
                result.update(incident=incident, status=incident["status"])
                expected = "blocked" if args.scenario == "blocked" else "recovered"
                result["checks"].append(check("expected_terminal_state", incident["status"] == expected,
                                              f"expected={expected}, actual={incident['status']}"))
                result["checks"].append(check("independent_injection_completed", "truth" in injection and "error" not in injection))
                injected_at = truth.get("injected_at", truth.get("wall_time")) if isinstance(truth, dict) else None
                if injected_at and incident.get("completed_wall_time"):
                    result["fault_to_terminal_ms"] = (incident["completed_wall_time"] - injected_at) * 1000
                    result["detection_delay_ms"] = (incident["detected_wall_time"] - injected_at) * 1000
    except Exception as exc:
        result.update(status="failed", error_type=type(exc).__name__, error=str(exc), source="ATTEMPTED")
        result["checks"].append(check("execution_completed", False, str(exc)))
    finally:
        try:
            adapter.close()
        except Exception as exc:
            result.update(status="failed", cleanup_error=f"{type(exc).__name__}: {exc}")
            result["checks"].append(check("owned_process_cleanup", False, str(exc)))
    return persist(result, output)


def parser():
    p = argparse.ArgumentParser(description="FabricSandbox: constrained operations agent and live fault experiments")
    sub = p.add_subparsers(dest="command", required=True)
    for command in ("tcp", "tcp-suite"):
        q = sub.add_parser(command, help="Real TCP experiments; standard library only, no GPU")
        q.add_argument("--output", type=Path, required=True)
        q.add_argument("--mode", choices=("rules", "llm"), default="rules")
        q.add_argument("--timeout", type=float, default=2.0)
        q.add_argument("--initial-channel", choices=("A", "B"), default="A")
        if command == "tcp":
            q.add_argument("--scenario", choices=("baseline", "no-repair", "recover", "blocked", "receiver-down"), default="recover")
    q = sub.add_parser("ddp", help="Real two-rank DDP; use existing torch environment; no weight downloads")
    q.add_argument("--output", type=Path, required=True)
    q.add_argument("--scenario", choices=("baseline", "no-repair", "recover", "blocked"), default="recover")
    q.add_argument("--gpus", required=True, help="Explicit experiment GPU pool, e.g. 0,1,2; first two active, rest spare")
    q.add_argument("--device", choices=("cuda", "cpu"), default="cuda")
    q.add_argument("--model", choices=("gpt2", "tiny"), default="gpt2")
    q.add_argument("--steps", type=int, default=20)
    q.add_argument("--checkpoint-every", type=int, default=5)
    q.add_argument("--fault-step", type=int, default=7)
    q.add_argument("--sequence-length", type=int, default=128)
    q.add_argument("--timeout", type=float, default=240)
    q.add_argument("--mode", choices=("rules", "llm"), default="rules")
    for command in ("reuse-profile", "planning"):
        q = sub.add_parser(command, help="Reuse original A1 data or original fixed-baseline planner")
        q.add_argument("--output", type=Path, required=True)
        if command == "reuse-profile":
            q.add_argument("--profile", type=Path, required=True)
    q = sub.add_parser("report", help="Build an offline HTML viewer from saved experiment JSON")
    q.add_argument("--input", type=Path, required=True)
    q.add_argument("--output", type=Path, required=True)
    sub.add_parser("doctor", help="Read-only environment check; does not install or modify packages")
    return p


def main():
    p = parser()
    args = p.parse_args()
    if args.command == "doctor":
        print(json.dumps({"python": sys.version, "executable": sys.executable,
                          "torch_installed": importlib.util.find_spec("torch") is not None,
                          "transformers_installed": importlib.util.find_spec("transformers") is not None,
                          "tcp_dependencies": "Python standard library; localhost sockets must be allowed",
                          "gpu_preflight": "ddp start performs CUDA/NCCL and device checks in a separate process"}, indent=2))
        return 0
    if args.command == "report":
        result = json.loads(args.input.read_text(encoding="utf-8"))
        if args.output.exists():
            p.error("Report output already exists; choose a new file")
        print(write_report(result, args.output))
        return 0
    if args.command == "ddp":
        if args.scenario != "baseline" and not args.checkpoint_every < args.fault_step < args.steps:
            p.error("Require checkpoint-every < fault-step < steps so failure follows a committed checkpoint")
        if args.device == "cpu" and args.model != "tiny":
            p.error("CPU validation mode requires --model tiny; this is not GPU evidence")
        if args.scenario == "recover" and len(args.gpus.split(",")) < 3:
            p.error("Recovery with replacement needs at least three allowed slots; use --scenario blocked for two")
    try:
        output = require_new_output(args.output)
        if args.command == "tcp":
            result = run_tcp(args, output)
        elif args.command == "tcp-suite":
            rows = []
            for scenario in ("baseline", "no-repair", "recover", "blocked", "receiver-down"):
                args.scenario = scenario
                result = run_tcp(args, require_new_output(output / scenario))
                rows.append({"scenario": scenario, "status": result["status"],
                             "passed": bool(result["checks"]) and all(x["passed"] for x in result["checks"])})
            atomic_json(output / "suite.json", rows)
            write_suite_index(rows, output / "index.html")
            return 0 if all(x["passed"] for x in rows) else 2
        elif args.command == "ddp":
            result = run_ddp(args, output)
        else:
            from .legacy_bridge import import_profile, run_planning_demo
            result = import_profile(args.profile, output) if args.command == "reuse-profile" else run_planning_demo(output)
            write_report(result, output / "report.html")
            print(json.dumps({"report": str(output / "report.json"), "viewer": str(output / "report.html")}, ensure_ascii=False))
            return 0
        return 0 if result.get("checks") and all(x["passed"] for x in result["checks"]) else 2
    except (ValueError, OSError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
