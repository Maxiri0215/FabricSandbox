# FabricSandbox 服务器真实 GPU 故障恢复执行总结

执行日期：2026-11-03 UTC。证据根目录：`/remote-home/share/project-user/Projects/DataNetwork/FabricSandbox_Agent/results/server_20261103_050617`。

结论：tiny 真实 DDP 恢复及 GPT-2 四组对照全部达到各自预期；独立逐文件验收通过。blocked 是正确反例，no-repair 保持未恢复；两者不计为恢复成功。

## 环境、版本和资源

沿用 A0–A5 解释器：`/remote-home/share/project-user/Projects/DataNetwork/FabricSandbox_v2/var/owner_env/bin/python`，已固化在 `run_env.sh` 的 `FABRIC_PY`。未安装/升级任何依赖，未使用系统 Python。

Python 3.12.15；PyTorch 2.7.1+cu128；Transformers 4.52.4；CUDA runtime 12.8；NCCL 2.26.2；NVIDIA driver 570.153.02。CUDA_DEVICE_ORDER=PCI_BUS_ID。

上传项目没有 `.git`，不虚构提交号。实际源文件 SHA256 见 `source_final_manifest.json`；其规范 JSON 摘要为 `f2d0c3ab75296178387917ca124c5606e5a58cfe11b96d118f1d5fab36499efc`。修改前文件和哈希见 `source_before/`、`source_before_manifest.json`；运行源码见 `source_final/`。

输出位于 GPFS 共享数据盘；启动时可用 33.57 TiB，超过 20 GiB。根分区不存实验输出，测试临时目录也设在本目录下。

| 物理 GPU ID | 角色 | UUID | 总显存 | 启动前占用 |
|---|---|---|---|---|
| 2 | 初始工作卡 | GPU-00000000-0000-0000-0000-000000000003 | 23.57 GiB | 15 MiB、0% |
| 3 | 初始工作卡 | GPU-00000000-0000-0000-0000-000000000004 | 23.57 GiB | 15 MiB、0% |
| 4 | 备用卡 | GPU-00000000-0000-0000-0000-000000000005 | 23.57 GiB | 15 MiB、0% |

GPU 0、1 当时各占 22665 MiB，未使用。nvidia-smi 的 compute-apps 列表在此容器中为空，无法枚举宿主其它任务 PID，不能将其解释为没有任务。选卡基于用户给定服务器授权、可见设备、连续空闲显存/利用率检查及 CUDA preflight；无全机排他调度锁。全部 worker 的真实 UUID 与 nvidia-smi 独立清单一致。

只清理本任务创建/登记的 launcher 和 worker；worker 检查 PID、进程组及 Linux 启动时钟身份。未使用 pkill、GPU reset 或全局网络修改。实验后退出检查见 acceptance_audit.json，设备占用见 gpu_after.csv。

## 测试与实验结果

| 项目 | 实际结果 | 验收 |
|---|---|---|
| doctor | 原解释器及依赖可见 | 通过 |
| 初始 tests/agent | 45 通过、0 失败、0 跳过（含真实 CPU Gloo 与 TCP 测试） | 通过 |
| 检查点保留修改后的 DDP 回归 | 14 通过、0 失败、0 跳过 | 通过 |
| tiny 真实 GPU 恢复冒烟 | recovered；最终报告步数 20 | 通过 |
| GPT-2 健康基线 | healthy；最终报告步数 20 | 通过 |
| GPT-2 故障、关闭恢复 | unrecovered_control；最终报告步数 7 | 通过 |
| GPT-2 Agent 恢复 | recovered；最终报告步数 20 | 通过 |
| GPT-2 两卡授权池、无备用 | blocked；最终报告步数 7 | 通过 |
| 旧 A1 导入 + compact/competition CPU 仿真 | 原件校验成功，两组各 2 作业完成，所有不变量通过 | 通过（仿真） |
| 独立原始证据审计 | 161 项检查通过 | 通过 |

所有 GPT-2 实测均为随机初始化 GPT-2 Small：12 层、768 hidden、12 heads、FFN 3072、vocab 50257、124439808 参数、FP32、AdamW(foreach=False)、每 rank batch=1、合成 token、sequence_length=128、world_size=2。每 5 步提交检查点，故障门控第 7 步，目标第 20 步。无权重/数据集下载，无外部 LLM API。

## 完整执行命令

下列命令在项目根目录执行；`run_env.sh` 固定 CUDA 顺序、可见卡和共享盘 TMPDIR。所有 Python 子命令都使用同一绝对解释器。原命令与开始/结束时间、退出码见 commands.jsonl；矩阵子命令见 gpu_matrix_01/command_*.json。

`doctor`，退出码 0：

```bash
/remote-home/share/project-user/Projects/DataNetwork/FabricSandbox_v2/var/owner_env/bin/python -m agent_demo doctor
```

`agent_tests_initial`，退出码 0：

```bash
/remote-home/share/project-user/Projects/DataNetwork/FabricSandbox_v2/var/owner_env/bin/python -m unittest discover -s tests/agent -p 'test_*.py' -v
```

`reuse_A1`，退出码 0：

```bash
/remote-home/share/project-user/Projects/DataNetwork/FabricSandbox_v2/var/owner_env/bin/python -m agent_demo reuse-profile --profile /remote-home/share/project-user/Projects/DataNetwork/FabricSandbox_v2/evidence/owner_20261102_174156/gpt2_profile.json --output /remote-home/share/project-user/Projects/DataNetwork/FabricSandbox_Agent/results/server_20261103_050617/reused_A1_01
```

`agent_tests_retention`，退出码 0：

```bash
/remote-home/share/project-user/Projects/DataNetwork/FabricSandbox_v2/var/owner_env/bin/python -m unittest discover -s tests/agent -p test_ddp.py -v
```

`gpu_matrix_01`，退出码 0：

```bash
/remote-home/share/project-user/Projects/DataNetwork/FabricSandbox_v2/var/owner_env/bin/python scripts/run_agent_gpu_matrix.py --gpus 2,3,4 --sequence-length 128 --output /remote-home/share/project-user/Projects/DataNetwork/FabricSandbox_Agent/results/server_20261103_050617/gpu_matrix_01
```

`acceptance_audit`，退出码 0：

```bash
/remote-home/share/project-user/Projects/DataNetwork/FabricSandbox_v2/var/owner_env/bin/python /remote-home/share/project-user/Projects/DataNetwork/FabricSandbox_Agent/results/server_20261103_050617/audit_evidence.py
```

`tiny_smoke` 实际子命令：

```bash
/remote-home/share/project-user/Projects/DataNetwork/FabricSandbox_v2/var/owner_env/bin/python -m agent_demo ddp --model tiny --scenario recover --gpus 2,3,4 --mode rules --steps 20 --checkpoint-every 5 --fault-step 7 --sequence-length 128 --timeout 240 --output /remote-home/share/project-user/Projects/DataNetwork/FabricSandbox_Agent/results/server_20261103_050617/gpu_matrix_01/tiny_smoke
```

`gpt2_baseline` 实际子命令：

```bash
/remote-home/share/project-user/Projects/DataNetwork/FabricSandbox_v2/var/owner_env/bin/python -m agent_demo ddp --model gpt2 --scenario baseline --gpus 2,3,4 --mode rules --steps 20 --checkpoint-every 5 --fault-step 7 --sequence-length 128 --timeout 240 --output /remote-home/share/project-user/Projects/DataNetwork/FabricSandbox_Agent/results/server_20261103_050617/gpu_matrix_01/gpt2_baseline
```

`gpt2_no_repair` 实际子命令：

```bash
/remote-home/share/project-user/Projects/DataNetwork/FabricSandbox_v2/var/owner_env/bin/python -m agent_demo ddp --model gpt2 --scenario no-repair --gpus 2,3,4 --mode rules --steps 20 --checkpoint-every 5 --fault-step 7 --sequence-length 128 --timeout 240 --output /remote-home/share/project-user/Projects/DataNetwork/FabricSandbox_Agent/results/server_20261103_050617/gpu_matrix_01/gpt2_no_repair
```

`gpt2_recover` 实际子命令：

```bash
/remote-home/share/project-user/Projects/DataNetwork/FabricSandbox_v2/var/owner_env/bin/python -m agent_demo ddp --model gpt2 --scenario recover --gpus 2,3,4 --mode rules --steps 20 --checkpoint-every 5 --fault-step 7 --sequence-length 128 --timeout 240 --output /remote-home/share/project-user/Projects/DataNetwork/FabricSandbox_Agent/results/server_20261103_050617/gpu_matrix_01/gpt2_recover
```

`gpt2_blocked` 实际子命令：

```bash
/remote-home/share/project-user/Projects/DataNetwork/FabricSandbox_v2/var/owner_env/bin/python -m agent_demo ddp --model gpt2 --scenario blocked --gpus 2,3 --mode rules --steps 20 --checkpoint-every 5 --fault-step 7 --sequence-length 128 --timeout 240 --output /remote-home/share/project-user/Projects/DataNetwork/FabricSandbox_Agent/results/server_20261103_050617/gpu_matrix_01/gpt2_blocked
```

## 故障、诊断、候选与实际恢复

第 7 步后实验驱动向已登记 rank 1（GPU 3）发送 SIGTERM。worker 自行写 exit-1.json；监测读取 launcher 状态、worker exit、含心跳和进度的 rank 状态。此故障由退出证据触发诊断；心跳/进度作为支持证据，未声称验证了独立的心跳超时检测能力。诊断为 worker_group_failed，按实验策略隔离执行槽 3；不认定 GPU 硬件损坏。

控制器/observe/diagnose 不读取 ground_truth.json；注入真值只由驱动及事后计分使用。AST 检查和已有伪造真值单测通过。注入线程与控制器共用适配器，属于逻辑隔离，不是租户安全隔离。

GPT-2 恢复候选：

| 候选 | 完整两卡组合 | 代价（更换槽数） | 约束结论 |
|---|---|---|---|
| ddp-v0-0 | 2, 3 | 0 | target includes an execution slot quarantined by recovery policy |
| ddp-v0-1 | 2, 4 | 1 | 可行，最终选中 |
| ddp-v0-2 | 3, 4 | 1 | target includes an execution slot quarantined by recovery policy |

旧进程 PID [277811, 277812] 已退出；executor.jsonl 记录旧组停止后才启动新组。新进程 PID [277983, 277984]，GPU 组合 2、4；两 rank 始终 world_size=2，健康 GPU 2 上的旧进程也重建。

恢复源：`checkpoints/checkpoint-e0-s5.pt`，step=5，SHA256 `c3240b7c3c5ce20a45df7e75aa8641bc7e8b2501608c469be61846428fdaec95`。commit 时间早于注入；文件仍保留在服务器，事后重新计算哈希一致。两个新进程实际报告加载相同 SHA256/step，然后分别产生 step 6–20 的训练事件，所有 loss 有限；最终目标 20 完成。

补充核对：恢复后 step 6–20 与健康基线同 rank、同步数 loss 的最大绝对差分别为 [0.0, 0.0]；原始逐步比较见 baseline_vs_restored_loss.json。这是本次运行的观察，不承诺其它硬件或配置逐位确定性。

无备用反例的授权池仅为 2、3；隔离 3 后剩余 2 不能凑齐固定两卡组。唯一候选被拒绝，无 apply、无新 epoch、无缩卡、无完成。这不表示整台 8 卡服务器没有空闲 GPU。关闭恢复对照仅运行旧组到第 7 步后终止。

## 性能指标与定义

检测、恢复出进度、完成训练和回滚分别统计。所有下列数字均来自本次事件时间戳，单次功能实测，不代表稳定分布或统计显著提升。

| 实验 | 检测延迟 | 故障→两 rank 首个恢复训练步 | 故障→训练完成 | 故障→控制器终态 | 回滚 |
|---|---:|---:|---:|---:|---:|
| tiny 真实 GPU 恢复冒烟 | 63.350 ms | 6.736 s | 7.681 s | 9.309 s | 2 |
| GPT-2 健康基线 | 不适用 / 未记录 | 不适用 | 不适用 | 不适用 | 未执行回滚 |
| GPT-2 故障、关闭恢复 | ≤ 23.086 ms（保留观测上界） | 不适用 | 不适用 | 不适用 | 未执行回滚 |
| GPT-2 Agent 恢复 | 70.765 ms | 12.555 s | 92.673 s | 94.940 s | 2 |
| GPT-2 两卡授权池、无备用 | 20.940 ms | 不适用 | 不适用 | 0.116 s | 未执行回滚 |

- 检测延迟：注入时刻 → 控制器首次 anomaly_detected。no-repair 没有控制器检测时刻，只报告保存下来的首次失败观测时间上界，不能伪称精确检测延迟。
- 注入起点使用注入器保存的 injected_at；它在持久化真值、发送 SIGTERM 之前取时，因此包含记录写入至发送信号的小段开销。worker 的 observed_at 是独立收到终止信号的时间，另给对应恢复延迟。
- 首次恢复有效训练：注入 → 两 rank 都完成恢复后的首个训练步（step 6）；包含清理、重建、检查点读取与重做第一步。恢复后的 step 6、7 是重做，首次超过故障前进度为 step 8。
- 训练完成：注入 → 两 rank 最晚 completed_at（step 20，含最终检查点）。控制器终态另含 launcher 退出等待和验收。
- 回滚：故障前共同报告 step 7 − 提交恢复 step 5 = 2。no-repair/blocked 有 2 个未提交步，但没有实际执行回滚。

GPT-2：从 worker 独立观测退出到两 rank 首个恢复步为 12.552 s；从注入到两 rank 超过故障前进度（step 8）为 13.188 s。健康基线从初始组启动到训练完成为 115.106 s；恢复组同口径总时间为 129.454 s。

训练步耗时按同一步两个 rank 的 train_step_ms 较大值统计：包含输入准备、前反向、优化器与 CUDA synchronize；不含随后显式 barrier、检查点 I/O、故障门控、重启或每步固定 50 ms pause。每个 epoch 剔除前 5 个训练样本后的中位数仅作描述，不将门控等待计入稳态性能。

| 实验 / epoch | 全部训练样本 | 剔除前 5 后样本 | 慢 rank 步耗时中位数 | 最大已报告门控等待 |
|---|---:|---:|---:|---:|
| tiny 真实 GPU 恢复冒烟 / epoch-0 | 7 | 2 | 9.707 ms | 0.209 s |
| tiny 真实 GPU 恢复冒烟 / epoch-1 | 15 | 10 | 3.423 ms | 0.000 s |
| GPT-2 健康基线 / epoch-0 | 20 | 15 | 102.890 ms | 0.000 s |
| GPT-2 故障、关闭恢复 / epoch-0 | 7 | 2 | 108.258 ms | 1.244 s |
| GPT-2 Agent 恢复 / epoch-0 | 7 | 2 | 106.871 ms | 1.351 s |
| GPT-2 Agent 恢复 / epoch-1 | 15 | 10 | 258.955 ms | 0.000 s |
| GPT-2 两卡授权池、无备用 / epoch-0 | 7 | 2 | 111.311 ms | 1.205 s |

## 修复、失败与限制

本轮没有 GPU 矩阵验收失败，没有覆盖或删除失败目录。初始单测日志中 “Stopped after failed acceptance” 是 mock 矩阵停止行为的预期测试输出，不能计为 GPU 实验失败。torchrun 旧组日志中的 SIGTERM traceback 是注入/清理的预期结果；NCCL 未显式传 device_id 提示及 Transformers 默认 loss 提示保留原日志，实际 UUID、loss 和执行验证均通过。

唯一功能修复：原 ddp_worker.py 在新提交后只保留最近两个 .pt，会删除恢复源并留下无法现场复核的旧 manifest。现改为保留全部已提交检查点；没有取消或放宽任何验收约束。真实 CPU 恢复测试新增对所有历史 manifest 对应文件存在及 SHA256 的检查，14 项回归通过；全部新 GPU 矩阵在修改后运行，无需重做 A0–A5。完整修改见 code_changes.diff，修改前后源码均保留。

只验证单机两个 rank 的进程故障与完整组恢复；未验证物理 GPU 故障、跨服务器/机架链路、RDMA 故障、Qwen 实际 TP 推理或 LLM 决策。单次重复数不足以作严谨性能对比。正式课程报告及组员独立交叉复核仍由团队完成，本次请求内的服务器执行和证据交付无剩余阻塞项。

## 旧实验复用与新增实测

旧 A1 原件：`/remote-home/share/project-user/Projects/DataNetwork/FabricSandbox_v2/evidence/owner_20261102_174156/gpt2_profile.json`；SHA256 `16c1c10024953a2f76f923aede859085756c975c158cc71db98382c48c5eaae2`。A1 原序列长度 512；本次 GPT-2 DDP 为 128，不能直接比较步耗时。导入仅重新运行 compact/competition CPU 仿真；恢复成本仍为 SYNTHETIC，网络容量仍是仿真假设。

A0 的原环境沿用并只读复核；A2 AllReduce 梯度基准、A5 两种 BF16 消息尺度原件与日志完整保留在原目录，并复制到 previous_A0_A5，源路径/哈希见 reuse_manifest.json。A2/A5 是同机独立通信证据，不换算为跨服务器链路带宽。A3 通过新导入入口复用 A1 参数，A4 旧状态测试和故障审计保留，未重跑旧 GPU 测量。

本次新增实测：tiny CUDA DDP 恢复、GPT-2 健康/无恢复/有恢复/无备用四组；控制器测试中的 CPU Gloo/TCP 为独立测试，未冒充 GPU 结果。

## 交付和打开方式

直接打开本目录 `index.html`（总览）、`gpu_matrix_01/index.html`（矩阵）、`gpu_matrix_01/gpt2_recover/report.html`（恢复详情）、`reused_A1_01/report.html`（旧 A1 导入）。所有页面可离线打开。

轻量证据包为本目录旁同名 `_evidence.zip`。保留 JSON/JSONL、日志、HTML、总结、diff、必要源码快照、命令和环境元数据；排除 Python 环境、API 密钥、.pt/.tmp 检查点及缓存。解压后从 index.html 进入，各相对链接保持有效。检查点留在服务器各 backend/checkpoints/ 下；checkpoint_inventory.json 记录大小和哈希。
