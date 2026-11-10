# 快速开始

本项目有三个独立入口。演示网页、仿真平台和真实执行 Agent 需要分别启动；启动其中一个不会自动启动另外两个。

以下命令均从仓库根目录执行。使用 Python 3.12 或 3.13 运行完整项目；可独立复制的 `demos/live` 支持 Python 3.10 及以上版本。

## 1 笔记本现场演示

现场演示仅依赖 Python 标准库，带有运行所需的仿真内核与历史证据副本。

```bash
python3 demos/live/server.py --port 0 --open
```

`--port 0` 由操作系统分配空闲端口，浏览器打开终端打印的网址。也可进入 `demos/live` 后使用系统对应的启动器。

```bash
cd demos/live
./启动演示.command
```

Windows 可双击 `demos/live/start_demo.bat`，或在 PowerShell 从根目录运行：

```powershell
py -3.12 demos/live/server.py --port 0 --open
```

页面包含三种模式：

| 模式 | 操作 | 应检查的结果 |
|---|---|---|
| 网络方案评估 | 选择或编辑拓扑，分析当前方案，再采用可行候选 | 冻结输入、拒绝理由、固定负载下的新仿真结果 |
| 真实通信故障 | 启动通信，观察 ACK 增长，再注入主通道故障 | 实际进程退出、备用通道、新数据 SHA 与 ACK |
| 3090 实测证据 | 查看同一历史事件的输入、诊断、候选、执行和验证 | 来源文件、恢复检查点、新 rank 进度与终态 |

不要直接打开 `web/index.html`，页面需要 Python 服务。演示期间保留终端，结束 TCP 实验时先点“停止实验”，最后在终端按 `Control+C`。单次 TCP 会话有超时限制，正式演示前启动新会话，避免误用预演记录。更完整的说明见 [演示 README](../demos/live/README.md)。

## 2 v2 仿真平台

### 创建环境

macOS/Linux：

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-demo.txt
```

Windows PowerShell：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-demo.txt
```

`requirements-demo.txt` 包含当前 Web/API 所需依赖。完整 `pyproject.toml` 还保留旧基础设施依赖；运行 v2 本地演示不需要启动 NATS 或 PostgreSQL。

### 启动与查询

```bash
.venv/bin/python scripts/serve_workloads.py --state-dir var/demo --auth
```

Windows 将 `.venv/bin/python` 换成 `.\.venv\Scripts\python.exe`。访问 [http://127.0.0.1:8000/workload](http://127.0.0.1:8000/workload)，将启动终端生成的令牌粘贴到页面。

- editor 可提交仿真，viewer 可读取结果和请求只读解释。
- `--auth` 每次启动生成新的共享角色令牌，不是个人账户。
- 固定 `--state-dir var/demo` 可保留 SQLite 运行记录；省略该参数时启动器使用新的临时目录。
- 服务重启后已完成结果保留，未完成记录标记为 `interrupted`，不会自动继续执行。
- 若 8000 端口占用，可添加 `--port 8001`，按实际打印的网址访问。

选择场景并提交后，页面轮询运行编号，随后读取结果与事件。事件列表是完成后的仿真结果，不是实时 GPU 训练日志。主页面为独立 HTML，不需要 Node.js 或 React 构建。

### 无 Web 的仿真批量运行

```bash
python3.12 scripts/run_v2_experiments.py --output results/simulation_01
```

该入口只使用标准库。查看输出目录中的输入、结果与汇总，不要把其中的合成耗时与真实训练耗时混用。

## 3 真实执行 Agent

### CPU 环境的工具流程

```bash
python3.12 -m agent_demo doctor
python3.12 -m agent_demo tcp-suite --mode rules --output results/tcp_01
python3.12 -m agent_demo planning --output results/planning_01
```

TCP 套件会创建本机通信进程并进行受控故障注入，需要环境允许本地监听端口。每轮选择新的输出目录；已有结果目录不会被静默覆盖。结果包含 JSON、工具调用轨迹与离线 HTML 报告。

`planning` 复用原静态规划器，属于兼容能力，不应把其输出当成 v2 动态 DDP/TP 仿真。

### GPU 服务器上的 DDP

使用已有、能运行 CUDA 分布式训练的 Python 3.12 环境。DDP 需要 PyTorch；GPT-2 场景还需要 `transformers`。运行前先检查环境：

```bash
python -m agent_demo doctor
```

在已明确分配给本实验的 GPU 池上先做 tiny 冒烟，再运行 GPT-2。下例 `0,1,2` 仅表示“初始两卡、备用一卡”，应替换为实际授权编号。

```bash
python -m agent_demo ddp --model tiny --scenario recover --gpus 0,1,2 --mode rules --output results/ddp_tiny_01
python -m agent_demo ddp --model gpt2 --scenario recover --gpus 0,1,2 --mode rules --output results/ddp_gpt2_01
```

GPT-2 使用配置创建结构并随机初始化，输入是合成 token，不下载预训练权重。默认 world size 为 2、目标 20 步、每 5 步提交检查点，在第 7 步后注入本实验 worker 退出。`--scenario` 还支持 `baseline`、`no-repair` 和 `blocked`，其中 blocked 分支仅使用给定池的前两张卡。

执行器保存模型、优化器、步数与各 rank 的随机状态。检查点会占用磁盘，启动前的最小空间检查不能替代整轮保留量预算。实验只处理登记且身份匹配的进程。不要将示例中的故障注入用于其他正在运行的训练。

规则模式不需要 API 密钥。`--mode llm` 是可选工具调用路径，不能绕过候选校验和最终验证；本仓库历史 DDP 恢复结果使用规则模式。

## 4 检查代码与测试

安装 Web/API 依赖后，从根目录执行：

```bash
.venv/bin/python scripts/run_tests.py
.venv/bin/python scripts/check_repository.py
```

`run_tests.py` 默认覆盖领域逻辑、进程管理和进程内 API，不启动真实 TCP/GPU 训练；`check_repository.py` 检查仓库文件、可搬迁入口和演示内核一致性。`scripts/run_tests.py --live` 额外包含真实 localhost TCP 与 CPU Gloo 进程测试，需本机网络权限与 PyTorch。`--local-env` 单独检查 `.venv`、`.env` 和编辑器配置，不作为默认运行前提。检查程序返回值与日志是本次运行的依据，历史测试数量见对应原始记录。

现场演示的无端口测试：

```bash
cd demos/live
python3 -m unittest discover -s . -p test_demo_model.py -v
python3 -m unittest discover -s tests -p test_custom_topology.py -v
python3 -m unittest discover -s tests -p test_topology_inprocess.py -v
```

真实 TCP/HTTP 测试需要允许监听端口的环境。前端 DOM 检查不能替代浏览器视觉检查，Windows 启动器也应在实际演示电脑上预演。

## 5 可选旧 React 页面

旧静态规划页面位于 `/legacy`，用于兼容早期功能。需要 Node.js 与 pnpm，在仓库根目录构建：

```bash
cd apps/web
pnpm install --frozen-lockfile
pnpm build
cd ../..
.venv/bin/python scripts/serve_workloads.py --state-dir var/demo --auth
```

然后访问 [http://127.0.0.1:8000/legacy](http://127.0.0.1:8000/legacy)。缺少构建产物时该入口会提示服务不可用；不影响 `/workload`。`infra` 的旧容器拓扑属于独立兼容配置，不是启动 v2 的前置步骤。
