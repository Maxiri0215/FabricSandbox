"""Export seven stages from one recorded GPU recovery, without executing GPU work.

The original evidence bundle is read-only. No checkpoint weights are copied.
Run with Python 3.9+ and the standard library.
"""
from pathlib import Path
from datetime import datetime, timezone, timedelta
import argparse
import hashlib
import json

HERE = Path(__file__).resolve().parents[1]
DEFAULT_BUNDLE = HERE.parents[1] / 'evidence' / 'gpu-case1'
SOURCE_RUN = 'gpu_matrix_01/gpt2_recover'
TZ = timezone(timedelta(hours=8))

CHECK_LABELS = {
    'two_rank_records': '两条 rank 记录',
    'all_ranks_completed': '两 rank 均完成',
    'target_steps_reached': '达到目标 20 步',
    'fixed_world_size': '通信组保持两张 GPU',
    'finite_final_loss': '最终 loss 有限',
    'launcher_exit_zero': 'launcher 正常退出',
    'restored_exact_checkpoint': '恢复源 step 与 SHA256 一致',
    'configured_slot_mapping': 'GPU 槽配置一致',
    'available_uuid_check_passed': '实际 GPU UUID 校验通过',
    'new_worker_pids': '两个 worker 均使用新 PID',
    'new_effective_progress': '出现检查点之后的新训练进度',
}


def source_context(bundle):
    """Return portable labels and only allowlisted public-copy metadata.

    The release marker is descriptive metadata, not a sanitizer. Source files
    must already have been sanitized before exporting a public copy.
    """
    bundle = Path(bundle).resolve()
    bundled = bundle == DEFAULT_BUNDLE.resolve()
    context = {
        'source_root': 'evidence/gpu-case1' if bundled else 'external_evidence_bundle',
        'source_bundle': 'gpu-case1' if bundled else 'external_evidence_bundle',
    }
    marker = bundle.parent / 'public_release.json'
    if not marker.is_file():
        return context
    try:
        release = json.loads(marker.read_text(encoding='utf-8'))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError('无法读取公开证据标记 public_release.json。') from exc
    if not isinstance(release, dict):
        raise ValueError('公开证据标记 public_release.json 必须为 JSON 对象。')
    if release.get('status') != 'sanitized_public_copy':
        return context
    context['privacy'] = {
        'status': 'sanitized_public_copy',
        'release_marker': 'evidence/public_release.json' if bundled else 'public_release.json',
        'pseudonymized_fields': ['personal_paths', 'host_identifiers', 'gpu_uuids', 'pci_addresses'],
        'execution_checks_scope': 'historical_original_execution_checks',
        'source_hash_scope': 'sanitized_public_copies',
        'checkpoint_digest_scope': 'retained_historical_digests_not_rehashed',
        'disclosure': (
            '这是脱敏公开副本：个人路径、主机标识、GPU UUID 和 PCI 地址均为代称。'
            '11 项执行检查记录原始实验的历史结论，不代表对这些代称重新验证了硬件。'
            '来源文件 SHA256 对应脱敏后的公开副本；检查点摘要保留历史记录值，'
            '本导出没有重新计算检查点权重的摘要。'
        ),
    }
    return context


def build(bundle, output):
    bundle = Path(bundle)
    context = source_context(bundle)
    root = bundle / SOURCE_RUN
    source_manifest = {}

    def read(path, lines=False):
        data = (root / path).read_bytes()
        source_manifest[path] = {
            'path': f'{SOURCE_RUN}/{path}',
            'sha256': hashlib.sha256(data).hexdigest(),
            'bytes': len(data),
        }
        if lines:
            return [json.loads(line) for line in data.decode().splitlines() if line.strip()]
        return json.loads(data)

    def refs(*paths):
        return [dict(source_manifest[path]) for path in paths]

    def iso(timestamp):
        return datetime.fromtimestamp(timestamp, TZ).isoformat(timespec='milliseconds')

    def fact(label, value, detail=None):
        item = {'label': label, 'value': value}
        if detail:
            item['detail'] = detail
        return item

    def stage(index, title, summary, facts, evidence, raw, presenter_note):
        return {
            'id': f'gpu-stage-{index}',
            'step': index,
            'title': title,
            'summary': summary,
            'facts': facts,
            'evidence': evidence,
            'raw': raw,
            'presenter_note': presenter_note,
        }

    config = read('backend/experiment_config.json')
    incident = read('agent/incident.json')
    exit_record = read('backend/epoch-0/exit-1.json')
    receipt = read('backend/execution-epoch-1.json')
    verify = read('backend/verification-epoch-1.json')
    resume = read('backend/resume-for-epoch-1.json')
    preflight = read('backend/preflight.json')
    executor = read('backend/executor.jsonl', True)
    old = [read(f'backend/epoch-0/rank-{i}.json') for i in range(2)]
    new = [read(f'backend/epoch-1/rank-{i}.json') for i in range(2)]
    logs = [read(f'backend/epoch-1/rank-{i}.jsonl', True) for i in range(2)]
    truth = read('backend/ground_truth.json')  # Post-run timing only; never a diagnosis input.

    old_start = next(x for x in executor if x['kind'] == 'group_started' and x['epoch'] == 0)
    stop = next(x for x in executor if x['kind'] == 'old_group_stopped' and x['epoch'] == 0)
    new_start = next(x for x in executor if x['kind'] == 'group_started' and x['epoch'] == 1)
    anomaly = next(x for x in incident['events'] if x['kind'] == 'anomaly_detected')
    selection = next(x for x in incident['events'] if x['kind'] == 'selection')
    validation = next(x for x in incident['events'] if x['kind'] == 'tool_result' and x.get('tool') == 'validate')
    diagnosis = incident['diagnosis']
    candidates = incident['candidate_rounds'][0]
    chosen = incident['selected_plan']
    cited = [x for x in incident['observation']['evidence'] if x['id'] in diagnosis['evidence_ids']]
    checks = verify['details']['checks']
    training = [[x for x in rows if x.get('status') == 'running' and 'train_step_ms' in x] for rows in logs]
    run_id = old[0]['run_id']

    # These invariants guard the extraction, not the original experiment itself.
    assert len(candidates) == 3 and sum(bool(x['feasible']) for x in candidates) == 1
    assert chosen['target'] == ['2', '4'] and chosen['cost'] == 1
    assert set(diagnosis['evidence_ids']) <= {x['id'] for x in incident['observation']['evidence']}
    assert receipt['status'] == 'started' and incident['status'] == 'recovered'
    assert len(checks) == 11 and all(checks.values()) and verify['recovered'] and verify['evidence']
    assert stop['wall_time'] < new_start['wall_time']
    assert receipt['rollback_reported_steps'] == 2 and resume['step'] == 5
    assert all(x['run_id'] == run_id for x in old + new)
    assert all(x['checkpoint_loaded']['sha256'] == resume['sha256'] for x in new)
    assert all([x['step'] for x in rows] == list(range(6, 21)) for rows in training)
    assert all(old[i]['pid'] != new[i]['pid'] for i in range(2))
    expected_uuids = {str(x['slot_id']): x['uuid'].lower() for x in preflight['devices']}
    assert all(x['device_uuid'].lower() == expected_uuids[str(x['slot_id'])] for x in new)

    identity_keys = ['run_id', 'epoch', 'rank', 'slot_id', 'pid', 'device_uuid', 'device_name', 'process_identity']
    before = [{k: x[k] for k in identity_keys if k in x} for x in old]
    after = [{k: x[k] for k in identity_keys + ['checkpoint_loaded'] if k in x} for x in new]
    progress = [{k: row[k] for k in ['rank', 'slot_id', 'pid', 'epoch', 'step', 'wall_time', 'loss', 'train_step_ms']} for rank in training for row in rank]
    keyframes = [dict(x, recorded_at=iso(x['wall_time'])) for x in progress if x['step'] in [6, 8, 20]]
    display_candidates = [dict(
        id=x['candidate']['id'],
        version=x['candidate']['version'],
        target=x['candidate']['target'],
        cost=x['candidate']['cost'],
        feasible=x['feasible'],
        rejection_reasons=x['rejection_reasons'],
        rejection_summary='通过约束校验' if x['feasible'] else '包含按恢复策略隔离的 GPU3',
    ) for x in candidates]

    stages = [
        stage(1, '输入与初始方案', '真实 GPT-2 Small 两卡训练，授权池包含一张备用 GPU。', [
            fact('模型', 'GPT-2 Small · 124,439,808 参数', '随机初始化、合成 token；运行真实前向、反向与 AdamW'),
            fact('训练设置', 'FP32 / 每 rank batch=1 / seq128'),
            fact('通信组', 'CUDA / NCCL DDP · world_size=2'),
            fact('初始方案', 'GPU [2,3]；备用 GPU4'),
            fact('目标与提交间隔', '20 步 / 每 5 步提交检查点'),
            fact('初始版本', 'version=0 / execution epoch=0'),
        ], refs('backend/experiment_config.json', 'backend/preflight.json', 'backend/executor.jsonl', 'backend/epoch-0/rank-0.json', 'backend/epoch-0/rank-1.json'), {
            'experiment_config': config,
            'initial_launch': old_start,
            'rank_identity': before,
            'preflight_devices': preflight['devices'],
        }, '先说明输入、初始资源和版本；当前页面只回放当时记录，未连接 GPU 服务器。'),
        stage(2, '真实异常', 'GPU3 上的 rank1 在 step7 被受控终止，worker 独立记录退出。', [
            fact('异常', 'SIGTERM (15) / worker 真实退出'),
            fact('异常位置', 'rank1 / GPU3 / PID277812'),
            fact('最后报告进度', 'step7'),
            fact('记录时间', iso(exit_record['observed_at'])),
        ], refs('backend/epoch-0/exit-1.json', 'agent/incident.json'), {
            'worker_exit': exit_record,
            'anomaly_observation': anomaly,
        }, '展示 worker 的独立退出记录；这里尚未得出硬件或链路故障结论。'),
        stage(3, '诊断证据', '当前观测确认训练组失败；恢复策略临时隔离执行槽3。', [
            fact('诊断代码', diagnosis['code']),
            fact('受影响执行槽', 'GPU3'),
            fact('证据引用', ', '.join(diagnosis['evidence_ids'])),
            fact('结论边界', '已证实进程退出；硬件根因未证实'),
        ], refs('agent/incident.json', 'backend/epoch-0/exit-1.json'), {
            'diagnosis': diagnosis,
            'cited_evidence': cited,
        }, '诊断只引用当前观测的 evidence_id。隔离槽3是一项恢复策略，不等于判定GPU3损坏。'),
        stage(4, '候选及拒绝理由', '枚举授权池中的完整两卡组，先检查可行性。', [
            fact('[2,3] · cost=0', '拒绝：包含隔离槽3'),
            fact('[2,4] · cost=1', '可行：通过当前全部约束'),
            fact('[3,4] · cost=1', '拒绝：包含隔离槽3'),
            fact('共同约束', '授权池、两卡完整组、版本、隔离与兼容检查点'),
        ], refs('agent/incident.json'), {
            'candidate_round': candidates,
            'candidates': display_candidates,
        }, '先展示所有候选与原始拒绝理由。此处不展示最终选择，也不暗示实时显存或网络性能已参与评分。'),
        stage(5, '所选方案', '规则按 (cost,id) 排序，选中唯一可行候选 [2,4]。', [
            fact('候选 ID', chosen['id']),
            fact('目标组', '[2,4]'),
            fact('代价', '1 个新引入的 GPU 槽'),
            fact('选择规则', selection['rule']),
            fact('显式校验', 'passed=true / rejection_reasons=[]'),
            fact('进程处理', 'GPU2 的位置保留，两个 rank 进程均重启'),
        ], refs('agent/incident.json'), {
            'selection_event': selection,
            'selected_plan': chosen,
            'validation_result': validation,
        }, '本轮只有一个可行方案。最少换卡不是网络性能最优，选中和校验通过也还不等于恢复成功。'),
        stage(6, '执行过程', '冻结 step5 恢复源，确认旧组退出，再在 GPU2、4 建立新组。', [
            fact('冻结恢复源', 'epoch0 / step5'),
            fact('检查点 SHA256', resume['sha256']),
            fact('回滚', 'step7 → step5，回滚 2 步'),
            fact('旧进程', 'GPU2 PID277811 / GPU3 PID277812'),
            fact('新进程', 'GPU2 PID277983 / GPU4 PID277984'),
            fact('版本变化', 'version 0→1 / execution epoch 0→1'),
            fact('执行回执', 'started', '启动成功还需要等待 verify'),
        ], refs('backend/execution-epoch-1.json', 'backend/resume-for-epoch-1.json', 'backend/executor.jsonl', 'backend/epoch-0/rank-0.json', 'backend/epoch-0/rank-1.json', 'backend/epoch-1/rank-0.json', 'backend/epoch-1/rank-1.json'), {
            'execution_receipt': receipt,
            'frozen_resume_manifest': resume,
            'stop_and_start': [stop, new_start],
            'before': before,
            'after': after,
        }, '执行回执仅为started。用独立的先停后启时间和新PID说明执行发生，再转入结果验证。'),
        stage(7, '新进度与验证结果', '两 rank 产生 step6–20 的新训练记录，11 项检查通过。', [
            fact('最终状态', 'recovered'),
            fact('检查结果', '11 / 11 通过'),
            fact('新训练进度', 'step6：从检查点继续；step8：超过故障前；step20：完成目标'),
            fact('物理 GPU 身份', verify['details']['mapping_verification']['mapping_verification_level']),
            fact('训练状态', '同一 step5 / SHA256 恢复，world_size=2'),
            fact('成功依据', '新 PID、实际 UUID、训练进度、有限 loss、正常退出'),
        ], refs('backend/verification-epoch-1.json', 'backend/epoch-1/rank-0.jsonl', 'backend/epoch-1/rank-1.jsonl'), {
            'verification': verify,
            'progress_keyframes': keyframes,
            'progress': progress,
        }, 'step6只是回滚后的重算，step8才越过故障前进度。verify有非空证据且recovered=true，控制器才能确认恢复。'),
    ]

    metrics = {
        'detection_ms': (incident['detected_wall_time'] - truth['injected_at']) * 1000,
        'first_shared_new_progress_ms': verify['details']['observed_failure_to_resumed_progress_ms'],
        'beyond_pre_failure_progress_ms': (max(next(x['wall_time'] for x in rank if x['step'] == 8) for rank in training) - truth['injected_at']) * 1000,
        'incident_terminal_ms': (incident['completed_wall_time'] - truth['injected_at']) * 1000,
        'rollback_steps': receipt['rollback_reported_steps'],
        'definitions': {
            'detection_ms': '注入时间 → Controller首次检测',
            'first_shared_new_progress_ms': 'worker独立退出时间 → 两rank均出现首个恢复训练步step6',
            'beyond_pre_failure_progress_ms': '注入时间 → 两rank均到达step8',
            'incident_terminal_ms': '注入时间 → Controller终态，包含后续训练与验证，不是纯恢复延迟',
        },
        'ground_truth_use': '仅用于实验结束后的时间端点核对，不作为Agent诊断输入',
    }
    payload = {
        'schema_version': 1,
        'mode': 'recorded_gpu_evidence',
        'is_live': False,
        'label': '真实 GPU 历史证据回放',
        'disclosure': '2026-11-03 单机 CUDA/NCCL DDP 实测证据。当前页面仅回放，不执行GPU任务。',
        'captured_at': iso(incident['started_wall_time']),
        'captured_until': iso(incident['completed_wall_time']),
        'timezone': 'Asia/Shanghai',
        'exported_at': datetime.now(TZ).isoformat(timespec='seconds'),
        'incident_id': incident['incident_id'],
        'run_id': run_id,
        'source_bundle': context['source_bundle'],
        'source_run': SOURCE_RUN,
        'agent_mode': incident['effective_mode'],
        'summary': '同一次真实GPT-2 Small DDP恢复事件：GPU[2,3]运行，rank1退出，规则Agent选[2,4]，由step5检查点恢复并完成step20。',
        'stages': stages,
        'candidates': display_candidates,
        'verification': {
            'status': incident['status'],
            'recovered': verify['recovered'],
            'checks_passed': sum(checks.values()),
            'checks_total': len(checks),
            'checks': [{'id': k, 'label': CHECK_LABELS[k], 'passed': v} for k, v in checks.items()],
            'mapping': verify['details']['mapping_verification'],
            'effective_progress_boundary': 'new_effective_progress本身只比较检查点step；超过故障前step7另由原始step8记录确认。',
        },
        'progress': progress,
        'metrics': metrics,
        'boundaries': [
            '历史证据回放，不是当前实时GPU运行。',
            '真实工作负载为随机初始化GPT-2 Small与合成token；不评价预训练质量。',
            '受控SIGTERM造成真实worker退出；未验证物理链路、RDMA或GPU硬件故障。',
            '本次effective_mode=rules，没有调用外部LLM。',
            '候选代价只计算新引入GPU槽；未按真实拓扑、链路带宽或恢复后训练性能排序。',
            '本导出不包含.pt权重文件；检查点哈希是源回执与加载记录中的已报告值。',
        ],
        'presentation_script': {
            'suggested_seconds': 90,
            'opening': '下面回放同一次服务器真实DDP实验。我会依次展示输入、异常、诊断、候选、选择、执行和验证，不把不同运行拼在一起。',
            'closing': '证据说明训练确实从同一检查点在新GPU组上恢复。它不证明网络性能改善，网络规划效果由前面的可交互仿真单独验证。',
            'pause_at_steps': [3, 4, 6, 7],
        },
    }
    if 'privacy' in context:
        payload['privacy'] = context['privacy']
        payload['disclosure'] += ' ' + context['privacy']['disclosure']
        payload['boundaries'].append(context['privacy']['disclosure'])
        payload['verification']['checks_scope'] = context['privacy']['execution_checks_scope']
    output.mkdir(parents=True, exist_ok=True)
    dest = output / 'gpu_evidence.json'
    dest.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    manifest = {
        'schema_version': 1,
        'mode': 'recorded_gpu_evidence',
        'incident_id': incident['incident_id'],
        'run_id': run_id,
        'source_root': context['source_root'],
        'source_run': SOURCE_RUN,
        'sources': list(source_manifest.values()),
        'output': {'path': 'data/gpu_evidence.json', 'sha256': hashlib.sha256(dest.read_bytes()).hexdigest(), 'bytes': dest.stat().st_size},
        'validation': {
            'same_incident_and_run': True,
            'exactly_seven_stages': len(stages) == 7,
            'three_candidates_one_feasible': True,
            'all_11_checks_passed': True,
            'both_rank_steps_6_through_20': True,
            'same_frozen_checkpoint_hash': True,
            'old_group_stopped_before_new_start': True,
            'new_worker_pids': True,
            'physical_uuid_matches_preflight': True,
        },
        'checkpoint_blob_copied': False,
        'checkpoint_blob_rehashed_by_this_export': False,
    }
    if 'privacy' in context:
        manifest['privacy'] = context['privacy']
        manifest['disclosure'] = context['privacy']['disclosure']
        manifest['validation_scope'] = 'export_consistency_of_sanitized_copies_not_hardware_reverification'
    (output / 'gpu_sources.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return {'path': str(dest), 'bytes': dest.stat().st_size, 'source_files': len(source_manifest), 'stages': len(stages), 'checks': len(checks), 'captured_at': payload['captured_at']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', type=Path, default=DEFAULT_BUNDLE,
                        help='原始证据目录，默认使用仓库 evidence/gpu-case1')
    parser.add_argument('--output', type=Path, default=HERE / 'build/gpu-evidence-export',
                        help='导出目录；默认写入 build/gpu-evidence-export，不覆盖历史 data')
    args = parser.parse_args()
    args.bundle = args.bundle.expanduser().resolve()
    if not args.bundle.is_dir():
        parser.error(f'证据目录不存在：{args.bundle}；请用 --bundle 指定原始证据目录。')
    if not (args.bundle / SOURCE_RUN).is_dir():
        parser.error(f'证据目录缺少 {SOURCE_RUN}：{args.bundle}')
    print(json.dumps(build(args.bundle, args.output), ensure_ascii=False))
