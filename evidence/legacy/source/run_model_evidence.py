#!/usr/bin/env python3
"""Collect reproducible CPU-model evidence from an unchanged FabricSandbox tree."""
from __future__ import annotations
import argparse
import copy
import csv
import hashlib
import json
import math
import platform
import runpy
import shutil
import subprocess
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

SOURCE_ROOTS = ('packages/fabric-domain/src', 'packages/event-contracts/src',
                'services/gateway/src', 'services/planner/src',
                'services/aggregator/src', 'services/evaluator/src')

def save(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--project', required=True, type=Path)
    ap.add_argument('--original', type=Path)
    ap.add_argument('--out', required=True, type=Path)
    ap.add_argument('--repeats', type=int, default=20)
    a = ap.parse_args()
    if a.repeats < 1:
        ap.error('--repeats must be positive')
    root, out = a.project.resolve(), a.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    checks = []
    def check(name, ok, observed=None):
        checks.append({'name': name, 'passed': bool(ok), 'observed': observed})
    for p in SOURCE_ROOTS:
        sys.path.insert(0, str(root / p))
    manifest = {'started_at_utc': datetime.now(timezone.utc).isoformat(),
                'project': str(root), 'original': str(a.original.resolve()) if a.original else None,
                'python': sys.version, 'platform': platform.platform(),
                'machine': platform.machine(), 'repeats': a.repeats,
                'scope': 'single-process CPU model, no real GPU/network traffic',
                'source_sha256': {}}
    for base in ('packages', 'services', 'scripts', 'tests'):
        for p in sorted((root / base).rglob('*')):
            if p.is_file() and p.suffix in ('.py', '.json') and '__pycache__' not in p.parts:
                manifest['source_sha256'][str(p.relative_to(root))] = hashlib.sha256(p.read_bytes()).hexdigest()
    manifest['runner_sha256'] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    save(out / 'manifest.json', manifest)
    try:
        from fabric_domain import (generate_topology, load_revision_file, FirstFitPlacementStrategy,
            RailAwarePlacementStrategy, rail_alignment_score, EvaluationConfig,
            evaluate_placement, replan_after_faults, Fault, FaultType)
        from planner import DistributedEvaluationCoordinator
        import evaluator.worker as worker

        # E1: configuration arithmetic, not hardware measurement.
        topology = generate_topology()
        t = topology.to_dict()
        leaves = []
        for leaf in t['leaves']:
            attached = [l for l in t['links'] if leaf['id'] in (l['endpoint_a'], l['endpoint_b'])]
            up = sum(l['capacity_gbps'] for l in attached if l['kind'] == 'spine_leaf')
            down = sum(l['capacity_gbps'] for l in attached if l['kind'] == 'leaf_node')
            leaves.append({'leaf_id': leaf['id'], 'uplink_gbps': up,
                           'downlink_gbps': down, 'ratio': down / up})
        counts = {'spines': len(t['spines']), 'leaves': len(t['leaves']),
                  'nodes': len(t['nodes']), 'gpus': topology.gpu_count, 'links': len(t['links'])}
        check('P4 resource counts', counts == {'spines':8, 'leaves':16, 'nodes':128, 'gpus':1024, 'links':256}, counts)
        check('P5 every leaf capacity ratio is 8', all(x['ratio'] == 8 and x['uplink_gbps'] == 3200 and x['downlink_gbps'] == 25600 for x in leaves))
        save(out / '01_topology.json', {'counts':counts, 'leaf_capacities':leaves, 'topology':t})

        # E2: save complete per-job placements and flow/link details.
        fixture = root / 'tests/fixtures/topologies/rail-aware-comparison.json'
        shutil.copy2(fixture, out / 'input_two_nodes.json')
        content = load_revision_file(fixture)
        strategies = {}
        for strategy in (FirstFitPlacementStrategy(), RailAwarePlacementStrategy()):
            placement = strategy.place(content.topology, content.jobs)
            evaluation = evaluate_placement(content.topology, content.jobs, placement, EvaluationConfig(flow_unit_gbps=200)).to_dict()
            strategies[strategy.name] = {'placement': placement.to_dict(), 'evaluation': evaluation,
                'alignment_by_job': {j.job_id:rail_alignment_score(j) for j in placement.assignments}}
        ff, ra = strategies['first_fit'], strategies['rail_aware']
        check('P7 A alignment 50% vs 100%', ff['alignment_by_job']['job-multi-01'] == .5 and ra['alignment_by_job']['job-multi-01'] == 1)
        for name, item in strategies.items():
            jobs = {j['job_id']:j for j in item['evaluation']['jobs']}
            check(f'P7 {name} A proxy remains 100', jobs['job-multi-01']['throughput_proxy_gbps'] == 100)
        shared = next(l for l in ra['evaluation']['links'] if l['link_id'] == 'leaf-0--node-0')
        check('P9 shared link carries 333.333333', math.isclose(shared['allocated_gbps'], 1000/3, abs_tol=1e-5), shared)
        save(out / '02_strategies_and_flows.json', strategies)

        # E3: observe the actual placement supplied to each worker's evaluator.
        # This wrapper delegates unchanged to the real function; it does not replace computation.
        real_evaluate = worker.evaluate_placement
        rows = []
        reference = None
        def ordered_assignments(parts):
            return sorted([j for p in parts for j in p['assignments']], key=lambda x:x['job_id'])
        for count, failures in ((1,set()), (2,set()), (3,set()), (2,{'worker-1'})):
            captured = []
            def observe(*args, **kwargs):
                placement = args[2] if len(args) > 2 else kwargs['placement']
                captured.append(copy.deepcopy(placement.to_dict()))
                return real_evaluate(*args, **kwargs)
            with patch.object(worker, 'evaluate_placement', side_effect=observe):
                result = DistributedEvaluationCoordinator(count).evaluate(content.to_dict(), 'rail_aware', 200, failures)
            placements = ordered_assignments(captured)
            ev = result.evaluation
            if reference is None:
                reference = {'placements':placements, 'evaluation':ev}
            unique_jobs = len({x['job_id'] for x in placements}) == len(placements)
            unique_gpus = len({g['gpu_id'] for x in placements for g in x['gpus']}) == sum(len(x['gpus']) for x in placements)
            conservation = []
            for link in ev['links']:
                flow_sum = sum(f['allocated_gbps'] for f in ev['flows'] if link['link_id'] in f['path_link_ids'])
                conservation.append({'link_id':link['link_id'], 'flow_sum_gbps':flow_sum,
                    'reported_gbps':link['allocated_gbps'], 'capacity_gbps':link['capacity_gbps'],
                    'passed':abs(flow_sum-link['allocated_gbps']) <= 1e-5 and flow_sum <= link['capacity_gbps']+1e-5})
            equal_p = placements == reference['placements']
            equal_e = ev == reference['evaluation']
            label = f'{count} workers; failed={sorted(failures)}'
            check('P11 placements: '+label, bool(captured) and equal_p and unique_jobs and unique_gpus)
            check('P11 evaluation: '+label, equal_e)
            check('P10 capacity accounting: '+label, all(x['passed'] for x in conservation))
            rows.append({'worker_count':count, 'failed_workers':sorted(failures),
                'placement_equal_to_one_worker':equal_p, 'evaluation_equal_to_one_worker':equal_e,
                'worker_job_assignments':result.assignments, 'reassigned':result.reassigned,
                'captured_worker_placements':captured, 'merged_gpu_assignments':placements,
                'evaluation':ev, 'link_checks':conservation})
        save(out / '03_sharding_placement_and_rates.json', rows)

        # E4: fault is a model input; no training process is migrated.
        fault_input = root / 'tests/fixtures/topologies/fault-replan.json'
        shutil.copy2(fault_input, out / 'input_three_nodes.json')
        fault_content = load_revision_file(fault_input)
        fault = replan_after_faults(fault_content, (Fault(FaultType.NODE_DOWN,'node-1'),), strategy_name='rail_aware', flow_unit_gbps=200).to_dict()
        before = {j['job_id']:j for j in fault['original_placement']['assignments']}
        after = {j['job_id']:j for j in fault['placement']['assignments']}
        check('P16 A before on node0+node1', set(before['job-multi-01']['node_ids']) == {'node-0','node-1'})
        check('P16 A after on node0+node2', set(after['job-multi-01']['node_ids']) == {'node-0','node-2'})
        check('P16 B exact GPU allocation unchanged', before['job-small-02']['gpus'] == after['job-small-02']['gpus'])
        check('P16 three GPU positions change', sum(m['moved_gpu_count'] for m in fault['migrations']) == 3)
        check('P16 complete and routable', fault['complete'] and fault['evaluation']['unroutable_flow_count'] == 0)
        save(out / '04_node_fault.json', fault)

        # E5: the synthetic diagnostic fixture is taken from the existing test.
        ns = runpy.run_path(str(root / 'tests/unit/test_security_assistant_pure.py'), run_name='evidence_fixture')
        case = ns['PureAssistantTest']('test_answer_preserves_exact_evidence_and_unit_meaning')
        case.setUp()
        synthetic = copy.deepcopy(case.run)
        answer = ns['explain_run'](case.run, '解释瓶颈')
        check('P17 assistant preserves input', synthetic == case.run)
        check('P17 correct synthetic evidence', '40 Gbps' in answer['answer'] and 'job-b' in answer['answer'] and '无法路由的流 2 条' in answer['answer'])
        save(out / '05_assistant_synthetic_case.json', {'source':'test_security_assistant_pure.PureAssistantTest.setUp',
            'kind':'constructed diagnostic input, not a full pipeline run', 'input':synthetic, 'answer':answer})

        # E6: reuse the project benchmark exactly; timing assertions are not fixed thresholds.
        command = [sys.executable, str(root/'scripts/benchmark_review.py'), '--root',str(root),
                   '--repeats',str(a.repeats), '--output',str(out/'06_benchmark.json')]
        if a.original:
            old = a.original.resolve()
            check('P10 identical fixture in both versions', (old/'tests/fixtures/topologies/rail-aware-comparison.json').read_bytes() == fixture.read_bytes())
            command += ['--compare-root',str(old)]
        with (out/'06_benchmark_stdout.log').open('w') as log:
            proc = subprocess.run(command, cwd=root, stdout=log, stderr=subprocess.STDOUT)
        check('benchmark exits successfully', proc.returncode == 0, proc.returncode)
        if proc.returncode:
            raise RuntimeError('benchmark failed; see 06_benchmark_stdout.log')
        bench = json.loads((out/'06_benchmark.json').read_text())
        if a.original:
            oldrows = bench['original_baseline']['fixture']['worker_results']
            check('P10 old version exposes over-capacity with 2 workers', any(x['worker_count'] == 2 and not x['failed_workers'] and x['capacity_violations'] for x in oldrows), oldrows)
        w128 = [x for x in bench['workload_scaling'] if x['requested_gpus'] == 128]
        check('P20 16 vs 120 machine pairs', {x['communication_mode']:x['cross_node_flows'] for x in w128} == {'ring_all_reduce':16, 'all_to_all':120})
        with (out/'ppt19_topology.csv').open('w',newline='') as f:
            w=csv.writer(f);w.writerow(['resource_gpus','job_gpus','placement_median_ms','evaluation_median_ms','placement_min_ms','placement_max_ms','evaluation_min_ms','evaluation_max_ms','repeats'])
            for x in bench['topology_scaling']:
                w.writerow([x['gpus'],x['requested_gpus'],x['placement']['median_ms'],x['evaluation']['median_ms'],x['placement']['min_ms'],x['placement']['max_ms'],x['evaluation']['min_ms'],x['evaluation']['max_ms'],a.repeats])
        with (out/'ppt20_workload.csv').open('w',newline='') as f:
            w=csv.writer(f);w.writerow(['job_gpus','mode','machine_pairs','evaluation_median_ms','min_ms','max_ms','repeats'])
            for x in bench['workload_scaling']:
                w.writerow([x['requested_gpus'],x['communication_mode'],x['cross_node_flows'],x['evaluation']['median_ms'],x['evaluation']['min_ms'],x['evaluation']['max_ms'],a.repeats])
    except Exception:
        (out/'error.log').write_text(traceback.format_exc())
        check('execution completed',False,'See error.log')
    summary = {'passed': bool(checks) and all(x['passed'] for x in checks), 'checks':checks,
        'not_covered':['browser operation','real GPU communication','cross-process workers','NATS/PostgreSQL services','multi-instance commit races'],
        'original_comparison': 'executed' if a.original else 'not_requested'}
    save(out/'summary.json',summary)
    print(f"Model evidence: {'PASS' if summary['passed'] else 'FAIL'}; {sum(x['passed'] for x in checks)}/{len(checks)} checks; {out}")
    return 0 if summary['passed'] else 1

if __name__ == '__main__':
    raise SystemExit(main())
