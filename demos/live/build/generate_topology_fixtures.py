#!/usr/bin/env python3
"""Generate real model outputs for the offline UI harness; no HTTP server needed.

Run with Python 3.10+: python3 build/generate_topology_fixtures.py
Then: node build/topology_ui_check.mjs
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from demo_model import plan_case, simulate_case, validate_topology


def main():
    three = {
        "nodes": [
            {"id": f"node-{rack}{i}", "rack_id": f"rack-{rack}",
             "gpu_count": 2, "memory_gib": 24}
            for rack in "abc" for i in (0, 1)
        ],
        "node_bandwidth_gbps": 100, "uplink_bandwidth_gbps": 50,
        "local_bandwidth_gbps": 200, "link_latency_us": 5,
        "local_latency_us": 2,
    }
    one = {"nodes": [{"id": "single", "rack_id": "one-rack",
                      "gpu_count": 8, "memory_gib": 24}]}
    large = {"nodes": [
        {"id": f"n-{i}", "rack_id": f"rack-{i // 4}",
         "gpu_count": 4, "memory_gib": 24} for i in range(16)
    ]}
    slow = {**three, "node_bandwidth_gbps": .001,
            "uplink_bandwidth_gbps": .001, "local_bandwidth_gbps": .001}
    fixtures = {
        "metadata": {
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "source": "actual demo_model calls; no mocked simulation metrics",
            "model_sha256": hashlib.sha256((ROOT / "demo_model.py").read_bytes()).hexdigest(),
        },
        "gpu": json.loads((ROOT / "data/gpu_evidence.json").read_text(encoding="utf-8")),
        "validations": {name: validate_topology(topology) for name, topology in
                        (("three", three), ("one", one), ("large", large), ("slow", slow))},
    }
    requests = {
        "default": {"uplink_gbps": 50, "memory_gib": 24,
                    "blocked_nodes": [], "steps": 30},
        **{name: {"topology": topology, "steps": 30, "blocked_nodes": []}
           for name, topology in (("three", three), ("one", one), ("large", large), ("slow", slow))},
    }
    for name, payload in requests.items():
        plan = plan_case(payload)
        plan["run_id"] = name  # Stable fixture identity, not a live server run.
        fixtures[name] = plan
        candidate = plan["selected_candidate"]
        if candidate is not None:
            fixtures[name + "_applied"] = {
                "run_id": name, "candidate_id": candidate["id"], "candidate": candidate,
                "evaluation": simulate_case({**payload, "variant": candidate["variant"]}),
            }
    fixtures["defaults"] = validate_topology(fixtures["default"]["topology"])
    output = ROOT / "build/topology_fixtures.json"
    output.write_text(json.dumps(fixtures, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    print(f"Generated {output.name}: {output.stat().st_size} bytes")


if __name__ == "__main__":
    main()
