# FabricSandbox Agent：三人执行手册

本手册对应 `agent_demo` 代码。交付包解压后的根目录为 `FabricSandbox_Agent`；下文所有命令均在该目录执行。现有项目中的同一代码位于 `/Users/project-user/Codex/Homework/output/fabric_refactor_20261102/FabricSandbox_v2`。

这次是在原项目上增加自动控制与真实执行后端。原有 A0–A5、规划器、DDP/TP 仿真保留；新增的核心是“观测 → 诊断 → 候选 → 校验 → 执行 → 新观测验证”。

## 1. 先确定哪些旧实验可以复用

| 已完成工作 | 本次怎么用 | 是否必须重跑 |
|---|---|---|
| A0：环境、设备、拓扑信息 | 继续使用当时验证过的 Python、PyTorch、CUDA、Transformers；检查本次允许占用的 GPU | 不需要重做环境搭建；启动前做只读检查 |
| A1：单 GPU GPT-2 profile | 用 `reuse-profile` 导入原始 `gpt2_profile.json`，复跑 compact/competition CPU 仿真 | 不必重测；导入器核验原始结果 |
| A2：独立 NCCL AllReduce | 保留原始日志与结果，说明本机通信实测基线及模型假设 | 不必重跑；不把它换算成跨服务器链路带宽 |
| A3：实测参数进入仿真 | 新导入入口会保留测量原件、SHA256、字段来源并重新生成输入 | 可以复用原数据；新入口负责统一证据 |
| A4：DDP/TP 状态、资源与故障仿真验证 | 继续作为正确性和领域约束证据 | 原证据保留；修改相关代码后跑对应回归 |
| A5：小消息 NCCL 测试 | 作为 TP 通信消息尺度的辅助证据 | 不必重跑；它不是实际 Qwen TP 推理 |

**必须新增的实验**是实际进程/通道故障后的控制流程：TCP 通道切换，以及真实 DDP 完整组重建和检查点恢复。原 A1 是单 GPU 训练，A2 是独立集合通信，它们没有验证真实 DDP 故障恢复。

本次真实 GPT-2 仍使用固定 GPT-2 Small 配置、随机初始化、FP32、AdamW、合成 token；不下载权重和数据集。默认输入长度为 128，用于减少联调时间。若原 A1 使用 512，可在最终 DDP 四组对照中统一指定 `--sequence-length 512`。不同输入长度的耗时不能直接比较；单 GPU profile 也不能当作真实 DDP 的迭代时间。

## 2. 三个人的任务与依赖

| 人员 | 可以独立开始的工作 | 必须交付 | 依赖 |
|---|---|---|---|
| 你，拥有 8×3090 | 原环境确认；DDP tiny 冒烟；GPT-2 四组实验；模型与恢复语义复核 | GPU 实验 JSON、日志、HTML；原 A1/A2/A5 结果；恢复检查结论 | 代码包、已允许占用的 GPU、数据盘空间 |
| 组员一，无 GPU | TCP 故障矩阵；另一初始通道的恢复；旧规划器固定基线对照 | TCP 结果；候选与拒绝原因；故障范围和规划算法解释 | Python 3.12+，允许本地 TCP 监听 |
| 组员二，无 GPU | Agent 规则模式与可选 LLM 模式；控制器测试；证据页面和报告 | 工具调用轨迹；版本/重复执行测试；演示与报告整合 | Python 3.12+；LLM 模式另需可用 API |

两位组员均不需要安装 CUDA、NCCL、PyTorch 或 Transformers，就能完成 TCP、旧规划对照和 A1 导入后的 CPU 仿真。GPU 结果生成后，把轻量证据发给他们，他们可以直接打开 HTML 和 JSON。

不要同时运行多个占用相同 GPU 的 DDP 实验。虽然服务器有 8 张卡，本轮仅需要 2 张工作卡加 1 张备用卡；其余卡无须占用。

## 3. 两位无 GPU 组员：先执行这套流程

### 3.1 进入代码包并检查 Python

解压到一个新目录，进入 `FabricSandbox_Agent`。使用 Python 3.12 或 3.13。以下 `python` 均指这个解释器；macOS 系统自带的旧版 Python 可能不满足要求。

```bash
cd FabricSandbox_Agent
python --version
python -m agent_demo doctor
python -m agent_demo --help
```

若已有 `uv`，但没有合适解释器，可创建纯标准库环境，不安装 GPU 包：

```bash
uv python install 3.12
uv venv --python 3.12 .venv
```

macOS/Linux 激活：

```bash
source .venv/bin/activate
```

Windows PowerShell 激活：

```powershell
.\.venv\Scripts\Activate.ps1
```

若 PowerShell 禁止激活脚本，可直接把后续命令中的 `python` 换成 `.\.venv\Scripts\python.exe`。不必为此修改全机策略。

### 3.2 运行完整 TCP 矩阵

```bash
python -m agent_demo tcp-suite --mode rules --output results/tcp_rules_01
```

这一条命令依次运行五个独立实验，每轮都会启动自己的接收端和两个 TCP 转发进程，结束后清理本轮进程：

| 子目录 | 操作 | 正确终态 |
|---|---|---|
| `baseline` | 正常通道传输 | `healthy` |
| `no-repair` | 中断当前通道，关闭自动恢复 | `unrecovered_control` |
| `recover` | 中断当前通道，Agent 探测、选路、切换并复查 | `recovered` |
| `blocked` | 两个通道均中断，没有可行方案 | `blocked` |
| `receiver-down` | 接收服务也不可达，无法隔离为单一转发通道故障 | `inconclusive` |

打开 `results/tcp_rules_01/index.html`。每个实验另有 `report.html` 和 `result.json`。网页是执行后的离线证据页，不是实时控制台；运行时终端会输出工具调用轨迹。

再用 B 作为初始通道，确认系统没有把“故障一定是 A”写死：

```bash
python -m agent_demo tcp --scenario recover --initial-channel B --mode rules --output results/tcp_recover_B_01
```

单独重做某个实验时使用新输出目录：

```bash
python -m agent_demo tcp --scenario recover --mode rules --output results/tcp_recover_02
```

### 3.3 运行原规划器的固定基线对照

```bash
python -m agent_demo planning --output results/legacy_planning_01
```

查看 `report.json`、`input.json`、`baseline.json` 和两个 `candidate_*.json`。这条命令复用原项目的拓扑、放置、故障影响分析和评估逻辑：固定同一份故障前方案，在同一故障和负载下比较不恢复基线、first-fit 候选和 rail-aware 候选。

它是 **规划代理模型评估**，标记 `planning_proxy`，不会真的迁移 GPU 进程。两种策略可能给出相同结果；如实报告即可。它与真实 TCP 执行共享展示主题，不代表任意交换机图已自动转换成 TCP 或 v2 仿真拓扑。

### 3.4 导入旧 A1 原始数据

你把原 `gpt2_profile.json` 发给任一组员即可。下面示例假定它放在包根目录的 `previous_A0_A5` 文件夹中：

```bash
python -m agent_demo reuse-profile --profile previous_A0_A5/gpt2_profile.json --output results/reused_A1_01
```

导入器会校验模型配置、参数数目、FP32 梯度字节数、原始样本与统计值、显存预算关系，并保存源文件及 SHA256。它使用实测计算和优化器耗时、实测 reserved memory 加原有明确策略余量，运行 compact 与 competition 两种 CPU 仿真。

检查点、重载、通信组初始化时间没有在 A1 中实测，导入后仍为 `SYNTHETIC`；网络容量也仍是仿真假设。不要手工把这些字段改成 `MEASURED`。

### 3.5 运行新增测试

```bash
python -m unittest discover -s tests/agent -p 'test_*.py' -v
```

测试中如出现 `skipped`，请阅读原因。端口绑定被环境禁止时，真实 TCP 测试不能运行；这不等于真实 TCP 验证通过。GPU 实测也不能由纯测试替代。

## 4. 你在 3090 服务器上的完整流程

### 4.1 继续使用 A0–A5 的原环境

把 `FabricSandbox_Agent` 解压到共享数据盘上的新目录。不要把原环境覆盖掉，也不要为本次实验升级 PyTorch/CUDA。

先按你原来的方式激活 A0–A5 使用的环境，再进入新目录。为避免切换目录后误用另一个环境，可在激活后记录解释器。以下 `/path/to/...` 是需要替换的示意路径：

```bash
source /path/to/A0_A5_environment/.venv/bin/activate
FABRIC_PY="$(command -v python)"
cd /remote-home/share/project-user/CourseProject/FabricSandbox_Agent
"$FABRIC_PY" -m agent_demo doctor
"$FABRIC_PY" -c 'import torch,transformers; print("torch",torch.__version__); print("transformers",transformers.__version__); print("cuda",torch.cuda.is_available()); print("visible_gpus",torch.cuda.device_count())'
nvidia-smi
df -h .
```

如果原环境路径不同，只需按实际路径激活；核心是让 `FABRIC_PY` 指向 A0–A5 已验证过的同一个 Python。新目录下直接执行 `uv run` 可能选择其他项目环境，本流程使用明确的解释器路径。

真实 GPU 实验要求 Linux；CPU TCP 可以在普通 macOS/Linux/Windows 上运行。GPT-2 预检要求输出所在磁盘至少有 6 GiB 空闲空间，预留给检查点和提交中的临时文件。四组实验分别保留检查点，整套建议准备约 20 GiB 数据盘空间。不要把输出放到空间紧张的容器 overlay 根分区。

确认三张卡确实已分配给本实验。以下 `0,1,2` 仅为示例：前两张初始工作，第三张备用；换成实际获准使用的设备 ID 或 UUID。代码会对子进程设置这份可见设备列表，并只清理本次创建的进程；它不会停止其他人的 GPU 任务。

### 4.2 先跑 tiny 恢复冒烟

如果希望一条命令依次完成 tiny 冒烟和 GPT-2 四组实验，可直接执行：

```bash
"$FABRIC_PY" scripts/run_agent_gpu_matrix.py --gpus 0,1,2 --sequence-length 128 --output results/gpu_matrix_01
```

脚本顺序执行，不并行抢占 GPU；某组验收失败就停止并保留证据。全部完成后打开 `results/gpu_matrix_01/index.html`。使用这条命令后，不必再重复下面的单项命令；单项入口用于排障和补跑。

```bash
"$FABRIC_PY" -m agent_demo ddp --scenario recover --model tiny --gpus 0,1,2 --mode rules --steps 20 --checkpoint-every 5 --fault-step 7 --timeout 240 --output results/ddp_tiny_recover_01
```

tiny 是小型真实 DDP 工作负载，验证通信组、检查点和进程控制流程；它不是 GPT-2 性能数据。若它未通过，先处理环境、进程和检查点错误，再进入 GPT-2。

不需要 GPU 的可选开发验证，仅供已经装有 CPU PyTorch 的机器：

```bash
python -m agent_demo ddp --device cpu --model tiny --scenario recover --gpus cpu0,cpu1,cpu2 --mode rules --output results/ddp_cpu_tiny_01
```

这里的 `cpu0` 等是实验执行槽标识，使用 Gloo，结果标记 `live_cpu_ddp`。两位组员不必为此额外安装 PyTorch。

### 4.3 正式 GPT-2 四组对照

以下统一使用输入长度 128。若决定与原 A1 的 512 token 配置对齐，请把四条命令全部改为 512；不要只改其中一条。

健康基线：

```bash
"$FABRIC_PY" -m agent_demo ddp --scenario baseline --model gpt2 --gpus 0,1,2 --mode rules --steps 20 --checkpoint-every 5 --sequence-length 128 --timeout 240 --output results/ddp_gpt2_baseline_01
```

相同工作负载，故障后关闭恢复：

```bash
"$FABRIC_PY" -m agent_demo ddp --scenario no-repair --model gpt2 --gpus 0,1,2 --mode rules --steps 20 --checkpoint-every 5 --fault-step 7 --sequence-length 128 --timeout 240 --output results/ddp_gpt2_no_repair_01
```

故障后由 Agent 重规划并恢复：

```bash
"$FABRIC_PY" -m agent_demo ddp --scenario recover --model gpt2 --gpus 0,1,2 --mode rules --steps 20 --checkpoint-every 5 --fault-step 7 --sequence-length 128 --timeout 240 --output results/ddp_gpt2_recover_01
```

只有两张允许使用的卡，无备用执行槽：

```bash
"$FABRIC_PY" -m agent_demo ddp --scenario blocked --model gpt2 --gpus 0,1 --mode rules --steps 20 --checkpoint-every 5 --fault-step 7 --sequence-length 128 --timeout 240 --output results/ddp_gpt2_blocked_01
```

最后一组限制的是**本实验授权资源池**，不能说整台八卡服务器没有空闲资源。固定两卡组中一个执行槽被策略隔离后，剩余一张不能组成完整组，因此正确结果为 `blocked`。

每组结束后，先看终端的 `status` 和 `checks_passed`，再打开该目录的 `result.json` 与 `report.html`。正式恢复实验的关键是 `incident.status == "recovered"`，并且执行后的验证检查通过。

### 4.4 故障实验具体发生了什么

1. `torchrun` 启动两个真实训练进程，内置自动重试设为 0；恢复动作由本项目控制器负责。
2. 每 5 步保存模型、优化器、逻辑步数和随机状态。检查点文件写完后才原子发布 manifest。
3. 第 7 步完成后，实验驱动使用门控让故障发生在确定的进度，避免训练过快结束。门控等待单独记录，不作为模型纯训练耗时。
4. Agent 已在独立监测循环中轮询观测；单独的实验注入线程终止本实验登记的一个 worker。真值写在 `ground_truth.json`，控制器与诊断函数不读取它。两者共享执行适配器，属于同一实验程序中的逻辑隔离，不是安全隔离的两个租户。
5. Agent 根据进程状态、退出记录和进度证据识别工作组失效，并按策略暂时隔离相应执行槽。它没有据此证明 GPU 硬件故障。
6. 候选枚举完整的两卡组合，排除不满足约束的组合，再按改动资源数选择。
7. 整个旧组清理后，新组从已提交检查点恢复。原健康 rank 进程也会重启，可以保留其 GPU，不能声称原进程始终不中断。
8. 验证器等待新组完成目标步数，检查 world size、有限 loss、资源映射、新 PID、加载的检查点哈希和恢复后的有效进度。有 CUDA UUID 时核对独立设备身份；旧 PyTorch 不提供 UUID 时明确标记 `logical_only`，只证明逻辑配置匹配。

## 5. 可选：接入真正的 LLM 工具调用

先确保规则模式通过。LLM 模式调用兼容 OpenAI Chat Completions 的接口，需要支持工具调用；没有 API 也可以完成全部规则模式实验。

Linux Bash 中可以这样设置，API key 通过隐藏输入读取，不写进代码或报告：

```bash
export FABRIC_AGENT_API_BASE='https://api.openai.com/v1'
export FABRIC_AGENT_MODEL='填写你的接口实际支持的模型名'
read -r -s -p 'API key: ' FABRIC_AGENT_API_KEY
export FABRIC_AGENT_API_KEY
```

其他兼容服务需把 `FABRIC_AGENT_API_BASE` 换成其 Chat Completions 前缀；代码会在后面加 `/chat/completions`。不在本手册中假定任何模型账号权限或免费额度。

先运行较短的 TCP 场景：

```bash
python -m agent_demo tcp --scenario recover --mode llm --output results/tcp_llm_01
```

服务器沿用固定解释器时：

```bash
"$FABRIC_PY" -m agent_demo tcp --scenario recover --mode llm --output results/tcp_llm_01
```

LLM 可选择 `observe / diagnose / propose / validate / apply / verify` 六个工具。候选 ID 由工具产生；LLM 不能提交任意 shell 命令、任意 GPU 操作或自己编写的执行方案。诊断规则、证据引用校验、约束和终态判定仍由代码负责。

检查 `agent/incident.json` 中的 `requested_mode`、`effective_mode` 和事件。只有确实发生了 `llm_tool_choice`，并且没有降级，才能把这次轨迹称为 LLM 驱动的工具调用。API 不可用或响应不兼容时，程序可降级为 `rules_fallback`，界面会保留这一事实。

密钥只放在环境变量中，不把终端完整环境、密钥、`.env` 文件发群或写入报告。

## 6. 结果在哪里，应该发给组员什么

| 文件 | 用途 |
|---|---|
| `result.json` | 本轮实验总结果、后端、验收检查、Agent 事件关联 |
| `report.html` | 可离线打开的证据页面，无需服务器/GPU/API |
| `agent/incident.json` | 诊断证据、全部候选、拒绝理由、选定方案、执行与验证 |
| `agent/trace.jsonl` | 按顺序保存的工具调用轨迹 |
| `backend/ground_truth.json` | 故障注入真值，仅用于事后核对 |
| `backend/tcp_snapshot.json`、`tcp_events.jsonl` | TCP 端点、通道和传输观测 |
| `backend/preflight.json`、`preflight.log` | DDP 环境、显存、磁盘与依赖检查 |
| `backend/torchrun-epoch-*.log` | 每次 DDP 组启动的实际训练日志 |
| `backend/epoch-*/rank-*.json` | rank、PID、资源映射、步数、loss 等状态 |
| `backend/execution-epoch-*.json` | 新组分配和检查点来源 |
| `backend/verification-epoch-*.json` | 恢复后的完整验证检查 |
| `backend/checkpoints/*.pt` | 大型恢复数据，保留在服务器，不必发群 |

可在包根目录运行下面的标准库脚本，只打包 JSON、JSONL、日志和 HTML。它不包含 `.pt`、环境目录或模型权重：

```bash
python - <<'PY'
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED
root = Path('results')
target = Path('agent_evidence_01.zip')
if target.exists():
    raise SystemExit('目标压缩包已存在，请换一个名字')
with ZipFile(target, 'w', ZIP_DEFLATED) as archive:
    for path in sorted(root.rglob('*')):
        if path.is_file() and path.suffix in {'.json', '.jsonl', '.log', '.html'}:
            archive.write(path, path.as_posix())
print(target.resolve())
PY
```

## 7. 如何判断实验完成

不要把命令退出码 0 统一解释为“恢复成功”。它表示该实验按其预期完成验收：`blocked` 和 `inconclusive` 反例也可以正确退出 0。真实恢复实验应同时满足：

- `status` 为 `recovered`，且 `checks_passed` 为真。
- `incident.verification.recovered` 为真，存在新的执行证据。
- TCP 实际切换通道，并完成校验正确的新数据块；失败块按原 ID 重试，接收端去重。
- DDP 实际使用新资源组合，旧进程已退出，新进程加载指定检查点并继续训练。
- 未把规划/仿真数据与真实 TCP/GPU 耗时混用。

`detection_delay_ms` 从故障注入计到监测循环首次发现异常。`fault_to_terminal_ms` 从故障注入计到本轮终态。对于 DDP 恢复，后者包含恢复后训练到目标步数的时间，不等于“第一个恢复步出现前的纯恢复时间”。需要报告更细阶段时，从 trace 和 rank/worker 事件中提取检测、决策、新组启动、首次新训练步的时间；不要把总值改名。注入时刻只在实验结束后用于计算指标，不参与诊断。

DDP 的执行回执还记录 `last_common_reported_step` 和 `rollback_reported_steps`；恢复验证记录 `observed_failure_to_resumed_progress_ms`，表示观测到的 worker 异常至所有 rank 首次产生恢复后有效训练步的时间。它与训练到目标终态的总时间分别报告。

同负载和同故障触发步的关闭恢复/开启恢复对照，能证明自动恢复是否有作用。默认各一次适合功能演示，不能据此声称稳定的性能分布或统计显著提升。

## 8. 常见问题

| 现象 | 处理方式 |
|---|---|
| `Output directory is not empty` | 改用 `_02` 等新目录；程序保护已有证据，不自动覆盖 |
| `No module named agent_demo` | 确认当前目录是包根目录，里面直接有 `agent_demo/` |
| Python 版本过低 | 换 Python 3.12/3.13；macOS 系统 Python 可能仍是 3.9 |
| `PermissionError` / TCP bind 被禁止 | 在普通本地终端运行，检查本地安全策略；受限沙盒内的跳过不能当真实成功 |
| DDP preflight 失败 | 先看 `backend/preflight.log`；核对原环境、CUDA、Transformers、磁盘和允许使用的卡 |
| `ModuleNotFoundError: torch` | 当前解释器不是 A0–A5 使用的环境；先核对 `doctor` 的 executable，不急于重装 |
| GPT-2 OOM | 确认卡由实验独占；先用 tiny、再统一使用长度 128；不要关闭校验或侵占其他任务资源 |
| 只有两张允许使用的卡 | 可跑 baseline/no-repair/blocked；需要替换槽的 recover 实验至少提供三个槽 |
| 超时或训练进度不足 | 保留失败目录，先查日志；确认只是启动慢再在新目录增加 `--timeout`，不要把未完成结果改为成功 |
| A1 导入被拒绝 | 使用旧脚本输出的原始 JSON；核对模型、配置和样本，不能手填字段伪造实测证据 |
| LLM 变为 `rules_fallback` | 查看 `policy_fallback` 类型和接口兼容性；规则恢复可验收，但不能报告成 LLM 全程决策 |
| TCP receiver 故障结果为 `inconclusive` | 这是预期负例：现有证据不足以把异常归因于单个转发通道 |

## 9. 五分钟演示与实现讲解

1. 展示一份实验输入、允许资源池和当前方案版本。
2. 在终端执行真实 TCP 恢复场景，展示工具调用顺序；打开生成的 `report.html`。
3. 展示探测证据、候选和拒绝理由；解释为什么备用通道满足约束。
4. 展示切换后的新传输证据，再展示双通道失效时的 `blocked`。
5. 展示服务器真实 DDP 恢复证据：旧组、新 GPU 分配、检查点、恢复后的步数。现场环境不稳定时播放同版本实跑录屏，不拿仿真替代实跑。

代码讲解按以下模块进行：

| 模块 | 应能回答的问题 |
|---|---|
| `contracts.py` | 不同后端为什么能接入同一个控制器，输入输出约定是什么？ |
| `controller.py` | 谁决定下一步、哪些动作可调用、如何限制循环、谁有权宣布成功？ |
| `tcp_backend.py` / `ddp_backend.py` | 诊断依据是什么，候选怎么生成，哪些约束会拒绝方案？ |
| `tcp_worker.py` / `ddp_worker.py` | 真实执行发生在哪里，进程、传输和检查点如何实现？ |
| `common.py` | JSON、版本与证据如何持久化，原子写解决什么问题？ |
| `legacy_bridge.py` | 原规划器和 A1 数据具体复用了什么，哪些模型边界没有混用？ |

当前 Agent 是本地单进程控制器，通过受约束工具管理本次登记资源；幂等保证限于适配器实例，不承诺控制器崩溃后的自动续跑或多租户资源锁。原 FastAPI 的权限功能仍属原服务路径，不能把直接 CLI 执行说成经过了 Web 角色认证。

## 10. 验证状态与领域边界

2026-11-03 目标服务器已完成真实 GPU 五组矩阵、TCP 五组矩阵、B 初始通道恢复和两组原规划器分析。当前 47 个不同 Agent 测试均有通过证据、无跳过；其中 45 项来自初始测试，14 项 DDP 回归与这 45 项重叠，另新增两项控制器反例。独立 CPU 审计 122 项、GPU 审计 161 项通过。详见 [统一入口](../../results/team_20261103_053106/index.html) 与 [执行总结](../../results/team_20261103_053106/team_cpu_execution_summary.md)。原编写环境的 CUDA/TCP 限制属于历史验证范围，不再代表此次服务器实跑状态。外部 LLM API、跨服务器网络和真实 Qwen TP 仍未验证。

真实 TCP 后端证明应用层通道探测和切换；真实 DDP 后端证明单机分布式训练工作组的进程恢复。它们不证明跨机架网络、RDMA、物理交换机故障、GPU 硬件修复或 Qwen KV 无损迁移。TP 仿真继续用于检验推理的领域约束，单独标注为仿真证据。

官方实现语义参考：

- [PyTorch torchrun](https://docs.pytorch.org/docs/stable/elastic/run.html)
- [PyTorch 故障恢复训练教程](https://docs.pytorch.org/tutorials/beginner/ddp_series_fault_tolerance.html)
- [OpenAI function calling](https://platform.openai.com/docs/guides/function-calling)

这些资料用于解释接口和恢复语义，不要求升级现有软件版本。
