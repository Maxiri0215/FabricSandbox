# FabricSandbox

**GPU 通信网络规划、故障诊断与恢复验证** · 数据通信网络课程项目

FabricSandbox 研究共享 GPU 资源中的两个问题：作业使用不同 GPU 时，为什么仍会竞争网络容量；成员故障后，如何恢复完整业务状态，并用新进度证明恢复成功。

仓库同时保留可交互的 DDP/TP 仿真平台、笔记本现场演示和真实执行 Agent。它们采用不同执行入口，分别输出模型结果、真实 TCP 传输证据和真实 DDP 训练记录。

公开实验材料已对个人路径、主机和设备标识脱敏；指标与恢复过程保持原记录含义。副本校验方式及历史摘要的区别见 [脱敏说明](docs/PRIVACY.md)。

## 选择运行入口

| 入口 | 主要内容 | 环境 | 结果范围 |
|---|---|---|---|
| **v2 仿真平台** | `/workload` 页面、共享容量、DDP/TP 状态机、持久运行记录、只读解释 | Python 3.12/3.13；Web 依赖 | 配置驱动的离散事件仿真，不运行神经网络 |
| **现场演示** | 网络方案评估、自定义拓扑、真实 TCP 主备故障切换、3090 历史证据回放 | Python 3.10+ 标准库；浏览器 | 每种展示明确标注仿真、当前 TCP 或历史 GPU 证据 |
| **真实执行 Agent** | 观察、诊断、候选校验、执行与验证；TCP 和两 rank DDP 后端 | Python 3.12/3.13；DDP 另需可用的 PyTorch/CUDA 环境 | 本机 TCP 或单机真实 GPU 训练恢复 |

主平台 Web 尚未直接调度真实 GPU 执行器。实际恢复实验使用规则策略，外部 LLM 不是复现实验的必需条件。

## 快速体验

以下命令从仓库根目录执行。

### 笔记本现场演示

无需安装 Python 第三方包，无需 GPU 或联网：

```bash
python3 demos/live/server.py --port 0 --open
```

使用终端打印的本地网址。保持服务终端运行，在页面选择“网络方案评估”或“真实通信故障”。TCP 演示结束后点击“停止实验”，再关闭服务。详细步骤见 [现场演示说明](demos/live/README.md)。

### v2 仿真平台

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-demo.txt
.venv/bin/python scripts/serve_workloads.py --state-dir var/demo --auth
```

打开 [http://127.0.0.1:8000/workload](http://127.0.0.1:8000/workload)，按终端提示使用 editor 或 viewer 令牌。固定 `--state-dir` 可保留并再次查询运行记录。`/workload` 直接使用仓库内页面，无需构建 React。

只运行仿真实验时，可以不安装 Web 依赖：

```bash
python3.12 scripts/run_v2_experiments.py --output results/simulation_01
```

Windows、真实 DDP、测试及旧页面启动方式见 [快速开始](docs/QUICKSTART.md)。

## 已实现的关键能力

- **共享容量与放置评估**：将通信映射到机内总线、节点网卡和机架方向上联；所有活跃流共同分配容量。
- **领域状态机**：DDP 整组准入、检查点回滚和旧事件隔离；TP 请求准入、KV 释放和副本故障处理。
- **可追溯运行**：规范化并冻结输入，计算 `input_hash`，通过 SQLite 绑定配置、状态、结果与事件。
- **有界异步服务**：默认一个执行线程、两个等待位；运行完成后查询结果与事件。协调器重启会将未完成记录标记为 `interrupted`。
- **受约束执行**：诊断引用当前证据，候选校验版本与授权范围，执行后必须通过验证才能报告恢复。
- **角色权限**：Web 支持 viewer/editor 共享令牌。CLI 独立限制授权 GPU 与登记进程，不继承 Web 角色认证。

组件职责、核心接口和限制见 [系统架构](docs/ARCHITECTURE.md)。

## 实验与证据

| 证据 | 已记录的观察 | 解释范围 |
|---|---|---|
| [v2 仿真](evidence/simulation/) | A 独占约 7.137 s；加入跨机架竞争作业后约 10.721 s；两个作业紧凑放置约 5.344 s | 固定合成参数下的对照，不能当作 GPU 实测耗时 |
| [3090 DDP 实验](evidence/gpu-case1/) | GPT-2 Small 结构、随机初始化与合成 token；GPU 2/3 受控 worker 故障后重建为 2/4，从 step 5 恢复并完成 step 20 | 2026-11-03 单机历史实测；四组对照各一次 |
| [现场演示](demos/live/) | 当前电脑可运行回环 TCP 进程，终止主通道后选择备用通道，并验证新数据哈希与 ACK | 应用层恢复，不代表物理交换机、RDMA 或跨主机 GPU 网络故障 |

仿真 `completed` 表示计算结束，作业或请求仍可能失败。Agent 的 `started` 回执只表示启动，恢复成功还要检查完整组、执行身份、恢复源和新进度。

## 仓库导航

| 目录 | 用途 |
|---|---|
| [`apps/web`](apps/web/) | v2 工作负载页面与旧 React 规划页面 |
| [`packages`](packages/) | 仿真内核、旧静态领域模型、事件契约 |
| [`services`](services/) | 网关、v2 应用服务及兼容服务模块 |
| [`agent_demo`](agent_demo/) | 真实 TCP/DDP 后端与恢复控制器 |
| [`demos/live`](demos/live/) | 可独立复制运行的现场演示 |
| [`scripts`](scripts/) / [`tests`](tests/) | 启动、实验、检查脚本与自动化测试 |
| [`evidence`](evidence/) | GPU、仿真和早期实验记录，按来源分开保存 |
| [`docs`](docs/README.md) | 当前使用说明、架构、提交说明与历史设计材料 |
| [`db`](db/) / [`infra`](infra/) | 旧数据库迁移与容器基础设施资料 |

`/legacy` 为可选旧规划页面，需要单独构建 React。NATS、旧分布式 worker 骨架和 PostgreSQL 设计不属于当前 v2 仿真主链路。历史文档中的计划、测试结论与“尚未实现”描述保留其时间背景，当前入口与状态以本 README 和 [文档索引](docs/README.md) 为准。

## 复现与提交

```bash
.venv/bin/python scripts/run_tests.py
.venv/bin/python scripts/check_repository.py
```

本次整理验证：主工程默认测试 **226 通过、3 项本机配置检查跳过**；现场演示 **25 项 Python 测试与 10 项 DOM 交互检查通过**；仓库静态检查 **341/341 通过**。真实 TCP/CPU Gloo 测试、GPU 重跑、Docker 启动及浏览器视觉不在本轮验证范围内。

完整验证范围、代码来源和实验限制见 [提交说明](docs/SUBMISSION.md)。仓库当前作为本地交付整理；发布、仓库地址及许可证由团队另行确定。
