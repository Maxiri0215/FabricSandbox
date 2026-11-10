#!/usr/bin/env python3
"""Run the GPU experiments sequentially in the EXISTING Python environment.

Stops at the first failed acceptance check; completed evidence is retained.
No package installation, model download or global GPU/process modification.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent_demo.common import atomic_json, read_json, require_new_output
from agent_demo.reporting import write_suite_index


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gpus", required=True, help="Allowed pool: first two active, at least one spare")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--sequence-length", type=int, default=128)
    parser.add_argument("--timeout", type=float, default=240)
    parser.add_argument("--skip-smoke", action="store_true", help="Use only after this exact environment passed tiny recovery")
    args = parser.parse_args()
    slots = [x.strip() for x in args.gpus.split(",") if x.strip()]
    if len(slots) < 3 or len(slots) != len(set(slots)):
        parser.error("Provide at least three distinct authorized GPUs")
    if not 2 <= args.sequence_length <= 1024 or not 1 <= args.timeout <= 1800:
        parser.error("Invalid sequence length/timeout")
    output = require_new_output(args.output)
    cases = ([] if args.skip_smoke else [("tiny_smoke", "tiny", "recover")]) + [
        ("gpt2_baseline", "gpt2", "baseline"), ("gpt2_no_repair", "gpt2", "no-repair"),
        ("gpt2_recover", "gpt2", "recover"), ("gpt2_blocked", "gpt2", "blocked")]
    rows = []
    for label, model, scenario in cases:
        pool = slots[:2] if scenario == "blocked" else slots
        command = [sys.executable, "-m", "agent_demo", "ddp", "--model", model,
                   "--scenario", scenario, "--gpus", ",".join(pool), "--mode", "rules",
                   "--steps", "20", "--checkpoint-every", "5", "--fault-step", "7",
                   "--sequence-length", str(args.sequence_length), "--timeout", str(args.timeout),
                   "--output", str(output / label)]
        print(f"\nStarting {label}; interpreter={sys.executable}; allowed_pool={pool}", flush=True)
        atomic_json(output / f"command_{label}.json", command)
        completed = subprocess.run(command, cwd=ROOT)
        result = read_json(output / label / "result.json", {})
        passed = completed.returncode == 0 and bool(result.get("checks")) and all(x["passed"] for x in result["checks"])
        rows.append({"scenario": label, "status": result.get("status", "process_failed"),
                     "passed": passed, "exit_code": completed.returncode})
        atomic_json(output / "matrix.json", rows)
        write_suite_index(rows, output / "index.html")
        if not passed:
            print(f"Stopped after failed acceptance: {label}. Inspect {output / label}. Use a NEW directory when retrying.", file=sys.stderr)
            return 2
    print(json.dumps({"all_experiments_accepted": True, "index": str(output / "index.html"),
                      "note": "Blocked is an expected negative result, not a recovered workload."}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
