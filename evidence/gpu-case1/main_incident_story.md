# 主展示事件：一次真实 GPT-2 DDP 完整恢复

- incident_id：`incident-20361f4c7c3942d3bd13e11d7da1dfbb`
- worker run_id：`b8169b2eba6c45578365d4e91879779b`
- 唯一证据目录：`gpu_matrix_01/gpt2_recover/`。以下所有阶段、时间、候选、执行和验证都来自此目录；不拼接 tiny、健康基线或另一运行的最好片段。
- 实际 Agent 模式：`rules`；终态：`recovered`；候选轮次 1；执行尝试 1（全部保留）。

## 1. 输入、约束与初始放置

GPT-2 Small，124439808 参数；随机初始化、FP32、AdamW(foreach=False)、合成 token、每 rank batch=1、sequence_length=128。固定 world_size=2、目标 20 步、每 5 步提交检查点；故障门控在第 7 步。授权 GPU 池 [2,3,4]；初始完整组 [2,3]，4 是备用。该池是本实验授权范围，不表示整台服务器只有三张卡。

原始输入：`backend/experiment_config.json` 的 `/model,/world_size,/steps,/checkpoint_every,/sequence_length,/allowed_slots,/initial_slots`；实际命令：`../command_gpt2_recover.json`；模型参数量：两份 `backend/epoch-0/rank-*.json#/parameter_count`。上列路径均以唯一证据目录为根（命令位于其父目录）。

| epoch | 活动方案版本 | launcher PID | rank PID / GPU / UUID |
|---:|---:|---:|---|
| 0 | 0 | 277806 | rank 0: PID 277811, GPU 2, UUID 00000000-0000-0000-0000-000000000003; rank 1: PID 277812, GPU 3, UUID 00000000-0000-0000-0000-000000000004 |
| 1 | 1 | 277964 | rank 0: PID 277983, GPU 2, UUID 00000000-0000-0000-0000-000000000003; rank 1: PID 277984, GPU 4, UUID 00000000-0000-0000-0000-000000000005 |

组的实际启动与版本来自 `backend/executor.jsonl`；UUID 来自各 rank 状态，独立期望值来自 `backend/preflight.json`，恢复验证级别为 uuid_verified。

## 2. 故障前已提交状态与首个异常

先提交的是 epoch 0、step 5 的检查点，提交时间 2026-11-03T05:12:24.138853+00:00；冻结源文件 `checkpoints/checkpoint-e0-s5.pt`，SHA256 `c3240b7c3c5ce20a45df7e75aa8641bc7e8b2501608c469be61846428fdaec95`。提交时间早于实验注入记录。

首个独立 worker 异常：`gpu_matrix_01/gpt2_recover/backend/epoch-0/exit-1.json`，kind=termination_signal，signal=15，rank=1，PID=277812，GPU 槽=3，已报告 step=7，observed_at=1793682745.6396751 Unix s（2026-11-03T05:12:25.639675+00:00）。另一 rank 后来的 SIGTERM 属于完整旧组清理，不能误当最初故障原因。

Controller 首次保存 failed 监测观测为 `agent/incident.json#/events/3`，随后 anomaly_detected 为 `/events/4`。`/detected_wall_time=1793682745.707569`；观测包含 launcher 状态、worker exit、两个 rank 的心跳、步数和进程存活状态。

`backend/ground_truth.json` 记录 injected_at=1793682745.6368043、目标 rank 1/GPU 3；**只用于本次导出后的时间对齐和核对，没有作为诊断输入**。独立 worker observed_at 与控制器 detected_wall_time 是不同时间端点。

## 3. 诊断、证据引用与判断边界

诊断 code：`worker_group_failed`；affected：`['3']`。解释：First worker exit observation on execution slot 3; temporarily isolate that slot by policy; restart the complete fixed-size group from a committed checkpoint

确定性边界：`process failure observed; hardware cause unproven`。这证明登记 worker 的进程故障，不证明 GPU 硬件损坏、物理网络断链或 RDMA 故障。隔离槽 3 是本实验恢复策略。

| 诊断 evidence_id | 类型 / 目标 | 原始字段 |
|---|---|---|
| e0-launcher-1793682745719445183 | launcher_status / group-0 | `agent/incident.json#/observation/evidence/0` |
| e0-exit-1 | worker_exit_observation / 3 | `agent/incident.json#/observation/evidence/1` |
| e0-rank-0-1793682745719775803 | worker_progress / 2 | `agent/incident.json#/observation/evidence/2` |
| e0-rank-1-1793682745719866654 | worker_progress / 3 | `agent/incident.json#/observation/evidence/3` |

执行源码的 observe/diagnose/_ranks 和 Controller 调用路径已审查；未读取 ground_truth.json。源码与审查结果在 `source_executed/`、`source_review.json`。注入线程与控制器共用适配器，是逻辑隔离，不是独立安全租户。

## 4. 全部候选与拒绝理由

| 轮次 | 候选 / 版本 | 目标完整组 | 代价（更换槽数） | 可行 | 原始拒绝理由 | 选中 |
|---:|---|---|---:|---|---|---|
| 1 | ddp-v0-0 / v0 | ['2', '3'] | 0 | False | target includes an execution slot quarantined by recovery policy | False |
| 1 | ddp-v0-1 / v0 | ['2', '4'] | 1 | True | 无 | True |
| 1 | ddp-v0-2 / v0 | ['3', '4'] | 1 | False | target includes an execution slot quarantined by recovery policy | False |

所有候选保持 world_size=2、all_rank_processes_restart=true。保留原组的代价虽然为 0，但包含被策略隔离的 GPU 3，因此不可行；目标 [3,4] 也因同一约束拒绝。详见 `candidates.csv` 中每行 JSON Pointer；候选自身没有独立时间戳，表中 proposal 时间明确关联返回该候选轮次的 propose 工具结果。

## 5. 选择和显式校验

选择 `ddp-v0-1`；规则 `minimum_declared_cost_then_id`；时间 1793682745.7609499 Unix s；原件 `agent/incident.json#/events/11`。在可行候选中按 (cost,id) 选择，不能称为任意全局最优。
显式 validate 回执 `/events/13/result`：`{"candidate_id": "ddp-v0-1", "passed": true, "rejection_reasons": []}`。apply 内部还会实时重校验。

## 6. 全部执行尝试、旧组清理与检查点加载

### 尝试 1：ddp-v0-1

- 候选基于方案版本 0；执行回执新版本 1；epoch 0 → 1。
- 旧 worker PID：[277811, 277812]；旧组停止记录 1793682746.9516473 Unix s，launcher exit_code=1。
- 新组启动 1793682746.9533405 Unix s，launcher PID=277964，GPU ['2', '4']；严格晚于旧组停止。
- 冻结恢复 manifest：`backend/resume-for-epoch-1.json`，与 `backend/execution-epoch-1.json#/checkpoint` 完全一致；step=5，SHA256=`c3240b7c3c5ce20a45df7e75aa8641bc7e8b2501608c469be61846428fdaec95`。
- last_common_reported_step=7，rollback_reported_steps=2；恢复是整组重建，健康 GPU 上的原进程也退出。
- 幂等键：`incident-20361f4c7c3942d3bd13e11d7da1dfbb:1:cbf774d60dd15d2d8935239d6f336d3f5da50687ceb0555240452267953e7eed`。同一尝试回执完整保存在 `agent/incident.json#/attempts/0`。
- 新 rank 0（PID 277983，GPU 2）实际加载：`{"step": 5, "sha256": "c3240b7c3c5ce20a45df7e75aa8641bc7e8b2501608c469be61846428fdaec95", "file": "checkpoints/checkpoint-e0-s5.pt"}`；来源 `backend/epoch-1/rank-0.json#/checkpoint_loaded`。
- 新 rank 1（PID 277984，GPU 4）实际加载：`{"step": 5, "sha256": "c3240b7c3c5ce20a45df7e75aa8641bc7e8b2501608c469be61846428fdaec95", "file": "checkpoints/checkpoint-e0-s5.pt"}`；来源 `backend/epoch-1/rank-1.json#/checkpoint_loaded`。

**不要用最新 manifest 替代恢复源**：导出时 `backend/checkpoint_manifest.json` 是 epoch 1、step 20、`checkpoints/checkpoint-e1-s20.pt`；此次加载的是冻结的 epoch 0、step 5。历史 manifest 和冻结副本均原样导出。

## 7. 恢复后的新训练进度与全部验证

| rank | 首个恢复训练步 | 有限 loss | 记录时间（Unix s） | 源字段 |
|---:|---:|---:|---:|---|
| 0 | 6 | 10.941666603088379 | 1793682758.191499 | `agent/incident.json#/verification/details/first_new_step_per_rank/0` |
| 1 | 6 | 10.917540550231934 | 1793682758.1915653 | `agent/incident.json#/verification/details/first_new_step_per_rank/1` |

两个 rank 都完成恢复后 step 6–20 的实际训练事件，loss 有限，并在目标 20 写 completed。step 6/7 为回滚后的重算；首次超过故障前进度为 step 8。每步原件位于新 epoch 的 rank-*.jsonl，timeline.csv 保留所有事件而非只选最快 rank。

| 验证检查 | 实际值 | 原始字段 |
|---|---|---|
| two_rank_records | True | `backend/verification-epoch-1.json#/details/checks/two_rank_records` |
| all_ranks_completed | True | `backend/verification-epoch-1.json#/details/checks/all_ranks_completed` |
| target_steps_reached | True | `backend/verification-epoch-1.json#/details/checks/target_steps_reached` |
| fixed_world_size | True | `backend/verification-epoch-1.json#/details/checks/fixed_world_size` |
| finite_final_loss | True | `backend/verification-epoch-1.json#/details/checks/finite_final_loss` |
| launcher_exit_zero | True | `backend/verification-epoch-1.json#/details/checks/launcher_exit_zero` |
| restored_exact_checkpoint | True | `backend/verification-epoch-1.json#/details/checks/restored_exact_checkpoint` |
| configured_slot_mapping | True | `backend/verification-epoch-1.json#/details/checks/configured_slot_mapping` |
| available_uuid_check_passed | True | `backend/verification-epoch-1.json#/details/checks/available_uuid_check_passed` |
| new_worker_pids | True | `backend/verification-epoch-1.json#/details/checks/new_worker_pids` |
| new_effective_progress | True | `backend/verification-epoch-1.json#/details/checks/new_effective_progress` |

验证级别：`uuid_verified`。最终 recovered=True；新训练步、已加载源、UUID、新 PID、固定 world size 和最终完成由完整检查共同支持。

## 8. 指标定义与绘图约束

- detection_delay_ms = **70.764542 ms**；注入器记录时刻 → `/detected_wall_time`。
- observed_failure_to_resumed_progress_ms = **12551.890135 ms**；独立 worker 退出 observed_at → 两 rank 中较晚的首个恢复训练步时间。
- fault_to_terminal_ms = **94940.064192 ms**；注入器记录 → `/completed_wall_time`，包含恢复后训练到目标步数和验证，**不是纯恢复延迟**。
- fault_to_terminal 使用 incident.completed_wall_time；trace 中 terminal 事件写入时间稍早，不能替换端点以挑选更快数字。
- 时间线将 Unix 秒、故障相对毫秒和 train_step_ms 分列；故障门控不计入训练步耗时。
- 所有指标来自这一次同 incident 的运行，不能称统计显著提升、P95 或普遍性能保证。

## 9. 导出时权重保留状态

此次仅做只读文件核验。所有 14 份提交 checkpoint（含 tiny）仍存在，重新计算 SHA256 均匹配各自 manifest；本事件实际恢复源也存在且匹配。详细时间、路径、实际哈希见 checkpoint_retention_at_export.json。权重不在 zip 内，只导出 metadata；若离开服务器，不能只凭 zip 再次计算权重哈希。

## 10. 建议 PPT 叙事顺序

按本页 1→7 顺序展示同一案例：输入/两卡放置 → 首个独立退出与监测 → 有引用的诊断 → 三个完整候选与两项拒绝 → 最少改动方案 → 旧组清理/新组/冻结检查点 → 新训练步与全部检查。健康、无恢复、无备用三个对照只能另页比较，不得插入本事件时间线。
