# Case 1 导出说明

> 公开副本：个人路径、主机及设备标识已匿名化。下文的原始导出、字节一致性及 SHA 记录描述当时采集阶段；当前文件以外层 [evidence_manifest.json](../evidence_manifest.json) 校验，见 [脱敏说明](../../docs/PRIVACY.md)。训练数值和事件顺序未改写。

此次只核验已有记录、复制原始证据、计算文件哈希和生成导出表；未运行训练、故障注入、恢复或测试，未修改训练/恢复逻辑。

- 原始实测根目录：`/remote-home/share/project-user/Projects/DataNetwork/FabricSandbox_Agent/results/server_20261103_050617`
- 本次导出目录：`/remote-home/share/project-user/Projects/DataNetwork/FabricSandbox_Agent/results/case1_export_20261103_072943`
- 主事件：`incident-20361f4c7c3942d3bd13e11d7da1dfbb`；worker run_id：`b8169b2eba6c45578365d4e91879779b`。
- 原始证据树完整保留在 `gpu_matrix_01/`，HTML 和 JSON 均保持原内容。报告中的服务器绝对路径属于原运行记录，便携入口使用 `gpu_matrix_01/index.html`。
- 阅读顺序：main_incident_story.md → timeline.csv / candidates.csv → experiment_overview.csv → 引用的原始文件。主案例所有阶段均来自 gpt2_recover 同一个 incident；其余实验作为独立对照。

## 1. 实际运行环境与资源

Python：`/remote-home/share/project-user/Projects/DataNetwork/FabricSandbox_v2/var/owner_env/bin/python`；版本 3.12.15。PyTorch 2.7.1+cu128；Transformers 4.52.4；CUDA runtime 12.8；NCCL 2.26.2；NVIDIA driver 570.153.02（server_run/gpu_before.txt）。

`CUDA_DEVICE_ORDER=PCI_BUS_ID`；授权池 GPU [2,3,4]，初始 [2,3]，恢复 [2,4]；blocked 仅授权 [2,3]。所有组固定两卡。服务器 GPU 0/1 等其他占用卡未用于本实验。原始进程占用及显存记录为 server_run/gpu_inventory.csv、gpu_processes_before.csv；本次只导出历史信息，没有重新占用或停止任何 GPU 进程。

| 物理 GPU ID | PyTorch 观测 UUID（nvidia-smi 使用 GPU- 前缀） | 名称 | PyTorch total_memory / bytes |
|---:|---|---|---:|
| 2 | 00000000-0000-0000-0000-000000000003 | NVIDIA GeForce RTX 3090 | 25307578368 |
| 3 | 00000000-0000-0000-0000-000000000004 | NVIDIA GeForce RTX 3090 | 25307578368 |
| 4 | 00000000-0000-0000-0000-000000000005 | NVIDIA GeForce RTX 3090 | 25307578368 |

nvidia-smi 各卡标称 24576 MiB；PyTorch 可见 total_memory 如上，不混用两个口径。输出位于共享数据盘，原始检查空闲空间为 36914485788672 bytes。环境记录只含选定的软件和进程参数，没有完整系统环境变量、API 密钥、Python 环境或模型缓存。

## 2. 历史完整命令（仅记录，本次未执行）

工作目录：`/remote-home/share/project-user/Projects/DataNetwork/FabricSandbox_Agent`。命令保留原始输出路径；这是当时实际命令记录，不能在原目录直接重复运行覆盖证据。环境参数来自 server_run/commands.jsonl 和 run_env.sh。

```bash
export FABRIC_PY=/remote-home/share/project-user/Projects/DataNetwork/FabricSandbox_v2/var/owner_env/bin/python
export CUDA_DEVICE_ORDER=PCI_BUS_ID
export CUDA_VISIBLE_DEVICES=2,3,4
export TMPDIR=/remote-home/share/project-user/Projects/DataNetwork/FabricSandbox_Agent/results/server_20261103_050617/tmp
```

入口命令及当时结果：

**doctor**，原始退出码 0；来源 server_run/commands.jsonl。

```bash
/remote-home/share/project-user/Projects/DataNetwork/FabricSandbox_v2/var/owner_env/bin/python -m agent_demo doctor
```

**agent_tests_initial**，原始退出码 0；来源 server_run/commands.jsonl。

```bash
/remote-home/share/project-user/Projects/DataNetwork/FabricSandbox_v2/var/owner_env/bin/python -m unittest discover -s tests/agent -p 'test_*.py' -v
```

**reuse_A1**，原始退出码 0；来源 server_run/commands.jsonl。

```bash
/remote-home/share/project-user/Projects/DataNetwork/FabricSandbox_v2/var/owner_env/bin/python -m agent_demo reuse-profile --profile /remote-home/share/project-user/Projects/DataNetwork/FabricSandbox_v2/evidence/owner_20261102_174156/gpt2_profile.json --output /remote-home/share/project-user/Projects/DataNetwork/FabricSandbox_Agent/results/server_20261103_050617/reused_A1_01
```

**agent_tests_retention**，原始退出码 0；来源 server_run/commands.jsonl。

```bash
/remote-home/share/project-user/Projects/DataNetwork/FabricSandbox_v2/var/owner_env/bin/python -m unittest discover -s tests/agent -p test_ddp.py -v
```

**gpu_matrix_01**，原始退出码 0；来源 server_run/commands.jsonl。

```bash
/remote-home/share/project-user/Projects/DataNetwork/FabricSandbox_v2/var/owner_env/bin/python scripts/run_agent_gpu_matrix.py --gpus 2,3,4 --sequence-length 128 --output /remote-home/share/project-user/Projects/DataNetwork/FabricSandbox_Agent/results/server_20261103_050617/gpu_matrix_01
```

**acceptance_audit**，原始退出码 0；来源 server_run/commands.jsonl。

```bash
/remote-home/share/project-user/Projects/DataNetwork/FabricSandbox_v2/var/owner_env/bin/python /remote-home/share/project-user/Projects/DataNetwork/FabricSandbox_Agent/results/server_20261103_050617/audit_evidence.py
```

**build_delivery**，原始退出码 0；来源 server_run/commands.jsonl。

```bash
/remote-home/share/project-user/Projects/DataNetwork/FabricSandbox_v2/var/owner_env/bin/python /remote-home/share/project-user/Projects/DataNetwork/FabricSandbox_Agent/results/server_20261103_050617/build_delivery.py
```

矩阵按 tiny → baseline → no-repair → recover → blocked 顺序执行。每个阶段的完整 argv 原件为 gpu_matrix_01/command_*.json：

**tiny_smoke**

```bash
/remote-home/share/project-user/Projects/DataNetwork/FabricSandbox_v2/var/owner_env/bin/python -m agent_demo ddp --model tiny --scenario recover --gpus 2,3,4 --mode rules --steps 20 --checkpoint-every 5 --fault-step 7 --sequence-length 128 --timeout 240 --output /remote-home/share/project-user/Projects/DataNetwork/FabricSandbox_Agent/results/server_20261103_050617/gpu_matrix_01/tiny_smoke
```

**gpt2_baseline**

```bash
/remote-home/share/project-user/Projects/DataNetwork/FabricSandbox_v2/var/owner_env/bin/python -m agent_demo ddp --model gpt2 --scenario baseline --gpus 2,3,4 --mode rules --steps 20 --checkpoint-every 5 --fault-step 7 --sequence-length 128 --timeout 240 --output /remote-home/share/project-user/Projects/DataNetwork/FabricSandbox_Agent/results/server_20261103_050617/gpu_matrix_01/gpt2_baseline
```

**gpt2_no_repair**

```bash
/remote-home/share/project-user/Projects/DataNetwork/FabricSandbox_v2/var/owner_env/bin/python -m agent_demo ddp --model gpt2 --scenario no-repair --gpus 2,3,4 --mode rules --steps 20 --checkpoint-every 5 --fault-step 7 --sequence-length 128 --timeout 240 --output /remote-home/share/project-user/Projects/DataNetwork/FabricSandbox_Agent/results/server_20261103_050617/gpu_matrix_01/gpt2_no_repair
```

**gpt2_recover**

```bash
/remote-home/share/project-user/Projects/DataNetwork/FabricSandbox_v2/var/owner_env/bin/python -m agent_demo ddp --model gpt2 --scenario recover --gpus 2,3,4 --mode rules --steps 20 --checkpoint-every 5 --fault-step 7 --sequence-length 128 --timeout 240 --output /remote-home/share/project-user/Projects/DataNetwork/FabricSandbox_Agent/results/server_20261103_050617/gpu_matrix_01/gpt2_recover
```

**gpt2_blocked**

```bash
/remote-home/share/project-user/Projects/DataNetwork/FabricSandbox_v2/var/owner_env/bin/python -m agent_demo ddp --model gpt2 --scenario blocked --gpus 2,3 --mode rules --steps 20 --checkpoint-every 5 --fault-step 7 --sequence-length 128 --timeout 240 --output /remote-home/share/project-user/Projects/DataNetwork/FabricSandbox_Agent/results/server_20261103_050617/gpu_matrix_01/gpt2_blocked
```

torchrun 子进程构造与 Python 解释器继承见 source_executed/agent_demo/ddp_backend.py；实际组启动 PID、epoch、slots、version、resume_manifest 见各 backend/executor.jsonl。该日志没有独立保存每次 torchrun 的完整 argv，不能把由源码重建的 argv 称作运行时原始记录。torchrun-epoch-*.log 均已保留。

## 3. 实验核验结论

| 实验 | 分类 | 实际终态 | 场景验收 | 目标训练完成 | 故障前进度 | 实际恢复 checkpoint / 回滚 | 最终完成步数 |
|---|---|---|---|---|---:|---|---:|
| gpt2_baseline | GPT-2 正式实测，124439808 参数 | healthy | 通过 | true | 不适用 | 未恢复 / 未执行 | 20 |
| gpt2_no_repair | GPT-2 正式实测，124439808 参数 | unrecovered_control | 通过 | false | 7 | 未恢复 / 未执行 | 未完成 |
| gpt2_recover | GPT-2 正式实测，124439808 参数 | recovered | 通过 | true | 7 | 5 / 2 | 20 |
| gpt2_blocked | GPT-2 正式实测，124439808 参数 | blocked | 通过 | false | 7 | 未恢复 / 未执行 | 未完成 |
| tiny_smoke | tiny 冒烟，3152 参数 | recovered | 通过 | true | 7 | 5 / 2 | 20 |

“验收通过”指满足该对照场景预期。no-repair 的预期是未恢复，blocked 的预期是拒绝执行；二者都没有完成训练，不能写为恢复成功，也没有“0 ms 完成”。

核验不仅使用退出码或 result.json 的成功文字，还读取每 epoch 两个 rank 的状态与 JSONL、独立退出、提交/冻结 manifest、执行回执、新进度及验证检查。export_acceptance.json 保存本次证据交叉核验；export_validation.json 保存导出完整性和源字段核验。原始 server_run/acceptance_audit.json 为当时的独立事后审计。

四组 GPT-2 全程 model=gpt2、device=cuda、world_size=2；实际各 rank parameter_count=124439808。source_executed/agent_demo/ddp_worker.py 的 GPT2Config/GPT2LMHeadModel(config)、DistributedDataParallel、NCCL init_process_group、loss.backward()、optimizer.step() 与 rank 训练记录共同支持真实两卡前向/反向/参数更新与梯度同步。使用随机初始化、合成 token，没有 from_pretrained 权重加载；这是恢复机制实验，不是预训练模型准确率评估。tiny 是 Linear(32,64)→ReLU→Linear(64,16)，输入 8×32，不能代替 GPT-2；其 CLI sequence_length=128 并不改变 tiny 输入形状。

恢复主事件候选 [2,3]、[2,4]、[3,4] 使用同一个故障前放置及版本；含隔离 GPU 3 的两项被拒绝，选择唯一可行 [2,4]。旧组退出后实际启动新组，双方 rank 均加载冻结 e0/s5 同一 SHA256，之后产生新 step 6–20，loss 有限，11 项验证全部 true，实际 UUID 匹配。blocked 没有备用时未降为单卡、没有执行尝试或 epoch 1。

## 4. 指标定义、空值与制图要求

| 实验 | detection_delay_ms | observed_failure_to_resumed_progress_ms | fault_to_terminal_ms | 最后列含义 |
|---|---:|---:|---:|---|
| gpt2_baseline | — | — | — | Not recorded / not applicable |
| gpt2_no_repair | — | — | — | Not recorded / not applicable |
| gpt2_recover | 70.76454162597656 | 12551.890134811401 | 94940.06419181824 | Includes restart, checkpoint loading, resumed training to target and verification; NOT pure recovery latency |
| gpt2_blocked | 20.940065383911133 | — | 115.86189270019531 | Time to blocked controller terminal, NOT training completion |
| tiny_smoke | 63.35043907165527 | 6732.889413833618 | 9309.37123298645 | Includes restart, checkpoint loading, resumed training to target and verification; NOT pure recovery latency |

- detection_delay_ms：ground_truth.injected_at → incident.detected_wall_time，仅事后评分使用注入时间。
- observed_failure_to_resumed_progress_ms：独立 worker exit.observed_at → 两个新 rank 中较晚的首个有效恢复训练步；恢复 step 6/7 是重算，超过故障前进度需到 step 8。
- fault_to_terminal_ms：注入时间 → incident.completed_wall_time。recover 包含重启、加载、恢复后训练到目标 20 步和验证；blocked 则是终止决策耗时，绝非训练完成或纯恢复时间。
- rollback_steps：执行回执中的 last_common_reported_step − 加载 checkpoint step，本事件 7−5=2。未执行恢复时留空，不拿 0 表示成功。
- train_step_ms 是 worker 在真实训练步骤周围记录的耗时；故障门控 gate_wait_seconds、检查点提交、进程启动及报告耗时不可掺入稳态训练性能。
- 单次运行，未做重复抽样、P95 或显著性检验。不将不同事件的最快片段拼接为一条故事。
- CSV 使用 UTF-8 BOM。缺失值为空串，不补 0；source_field_paths_json / extraction_provenance.json 提供原文件、RFC 6901 JSON Pointer 和派生方法。jsonl 行号从 1 起；JSON 数组下标从 0 起。
- timeline.csv 记录绝对 Unix 秒和相对故障毫秒。Controller 未显式携带 epoch 的事件关联此前最近 group_started，epoch_origin / active_version_origin 明确标为推导；候选版本独立于当前活动版本。候选 proposal 时间来自 propose 工具返回事件，不伪称每个候选自带时间戳。
- 时间线保留全部来源记录；同一里程碑可能同时有 executor、rank、manifest、controller 记录，因此不要按行数统计独立故障次数或训练步数。时间戳来自同一服务器 wall clock，未验证跨主机时钟同步。

## 5. 诊断边界与 ground_truth

ground_truth.json 原样保留，仅用于事后时间对齐和注入目标核对。诊断读取登记进程/launcher 状态、独立 worker exit、心跳和进度，没有读取注入真值。source_review.json 和实际源码记录审查依据；这是逻辑隔离，注入器和控制器共用适配器，并非安全租户隔离。诊断边界为 process failure observed; hardware cause unproven：本次证明真实进程退出和 DDP 恢复，不能证明物理 GPU 故障、交换机故障、RDMA 或跨服务器恢复。

## 6. 代码版本、历史修复与失败记录

当前目录没有可用 Git 仓库，因此没有 commit/status/diff 可从 Git 提取。source_provenance.json 保留 Git 探测结果；实际执行源码按原始 source_final_manifest 校验后复制到 source_executed/，包含完整 agent_demo/、矩阵脚本、当时 tests/agent/。source_current/ 保存导出时的实际源码和相关文档；生产源码与执行快照哈希一致。SHA 清单提供文件级版本标识。

GPU 矩阵执行前的唯一生产修复：取消 ddp_worker.py 仅保留最近两个 .pt 的轮转删除，保留全部已提交 checkpoint，避免实际恢复源被后续提交删除；test_ddp.py 增加对应保留及哈希断言。原始 diff 为 server_run/code_changes.diff，修复前源码为 history/source_before_gpu_fix/，修复后为 source_executed/。这是已有修复，本次没有更改它。

矩阵之后的团队 CPU 工作新增 test_budget_and_evidence.py，并更新 README、AGENT_EXECUTION、architecture_report、rubric_mapping；相关原始 diff 与日志在 history/post_gpu_cpu_work/，源文件在 source_current/。这些后续测试和文档不得称作 GPU 运行时已经使用的版本。

历史初始测试 45 通过、0 失败、0 跳过；保留策略修改后受影响的 14 项通过、0 失败、0 跳过（包含在 45 中，不累加）；后续新增 2 项通过。单元测试包含替身和 tiny CPU/GPU 路径，测试通过不能代替正式 GPT-2 四组实测。本次没有重新跑这些测试。

本项目 results 下发现的原始 GPU 矩阵仅 server_20261103_050617/gpu_matrix_01，其余为团队交付副本和本次导出副本。没有发现额外失败矩阵或重复恢复尝试；主事件只有 1 次 attempt、1 轮候选，全部保留。故障阶段的 SIGTERM、非零旧 launcher 退出、torchrun 失败栈是预期注入和清理证据，原样保留，不能删去或误记为正常训练。原始测试日志中的模拟失败输出也保留，不能混写为正式 GPU 实验失败。

## 7. 冻结恢复源、权重和完整性

主事件实际加载 backend/resume-for-epoch-1.json 中的 checkpoint-e0-s5.pt；冻结记录与 execution-epoch-1.json#/checkpoint 及双方 rank 的 checkpoint_loaded 一致。最新 checkpoint_manifest.json 已指向 e1/s20，不能反向拿它当恢复源。所有历史 checkpoints/manifest-*.json 均导出。

导出时服务器仍存在 14/14 个已提交权重，逐文件重新读取字节计算 SHA256，均匹配 manifest；详见 checkpoint_retention_at_export.json 的时间和实际哈希。本次没有 torch.load、重新训练或重新执行恢复。权重总计 14935014530 bytes，全部留在原服务器目录，不在压缩包内。没有历史提交权重被轮转删除的缺口。

copy_provenance.json 记录每个原件与导出副本的路径和 SHA256。sha256_sources.json 覆盖实际源码/测试/历史源文件；sha256_evidence.json 覆盖其余证据和生成材料。sha256_all.json 与 SHA256SUMS 覆盖全部内容文件和分类清单（仅排除这两份总清单本身，避免自引用）。zip 外部另有 SHA256 与校验回执，记录完整压缩包哈希、CRC 检查及逐成员哈希核验。

原始 HTML 是离线结果报告，未改写成实时监控；当前 CLI Agent 没有因此接入原 Web 角色认证。压缩包保留每份原件，源文件中密钥环境变量名称/占位符属于接口说明，未导出密钥值。

## 8. 旧 A0–A5、分类与缺失清单

previous_A0_A5/ 含旧 A1 gpt2_profile.json/log、A2/A5 的 allreduce_gradient 与两种 tp_message 原始 JSON/log，以及环境/拓扑/审计辅助资料；reuse_manifest.json 提供原始路径与哈希。A1 已成功导入，reused_A1_01/ 是旧 profile 驱动的 CPU 仿真输出，未重测 GPU。A1 sequence_length=512，与本次 DDP 的 128 不可直接作性能公平对照；旧 A2/A5 是同机独立 NCCL 测量，不代表跨服务器链路带宽。

分类：本次四组 GPT-2 是 live_gpu 实测；tiny_smoke 是单独的真实 DDP 小网络冒烟；reused_A1_01 是仿真；候选 cost 是更换槽数的规划代理指标；单元测试可能使用测试替身。恢复与 blocked 的实际 effective_mode=rules。baseline/no-repair 未启动 Agent，虽 CLI 传 --mode rules，actual_agent_mode 留空。没有实际外部模型参与，LLM 未验证，不得把规则轨迹称作 LLM。

必需的已产生原始证据无缺失；旧 A1/A2/A5 原始文件也已找到。以下为不适用、未记录或有意排除：

- baseline/no-repair 没有 Agent incident/trace 和实际 Agent 模式；这与控制流程一致。baseline 无故障指标；no-repair 没有 controller detection/terminal 指标，也没有恢复进度或任务完成时间。
- no-repair/blocked 没有恢复执行回执、新 epoch、加载步数、回滚或最终完成步数；最终观测 step=7 单列为 last_observed_step。blocked 的有记录终态延迟只指拒绝决策。
- 正常成功退出的 rank 不产生 failure exit JSON；baseline 无 ground_truth.json；这些预期缺席逐项列于 evidence_inventory.json，不以空文件伪造。
- 未导出 .pt、未提交 .tmp、Python 环境、模型缓存或密钥；14 个提交权重当前均存在服务器并已核验。
- 无 Git 提交标识，以实际源快照/哈希替代。每次 torchrun 完整 argv 未单独记录，但 CLI 原始 argv、执行器源码、配置与 PID/epoch/slot 回执齐全。
- LLM API、跨机/RDMA/物理故障、统计显著性/P95 未验证。详细空字段理由见 missing_evidence.json；不要补零或推断为成功。

## 9. 导出工具自身的核验修正

首次压缩前校验中，60 项仅 ISO 显示时间检查失败：原始 Unix 秒保留浮点精度，ISO 列按微秒显示，错误地要求 ISO 反解析值与亚微秒原值完全相等。已修正为严格校验 ISO 字符串等于规定的 UTC 微秒格式；原始 Unix 秒对原始字段的精确相等、相对毫秒、排序及 epoch 检查仍保留。原始 CSV 和实测证据未更改。失败核验、修正前脚本、diff 和诊断例在 history/export_validation_fix_01/，属于导出工具修正，不是 GPU 实验失败或训练逻辑修改。utc_iso 仅用于阅读，制图计算使用 wall_time_unix_s / since_fault_ms。

此次没有生成 PPT，交付的是可直接供另一助手制图和制作 PPT 的可编辑源稿、CSV、原始证据和实际代码。
