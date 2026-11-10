# 系统架构

FabricSandbox 将网络容量计算、业务状态管理和恢复验证作为三个相互约束的问题处理。当前代码包含 v2 仿真主链路、独立真实执行 Agent 和便于课堂展示的现场演示。三者共用部分领域逻辑，但没有组成自动控制真实 GPU 集群的统一平台。

## 1 v2 仿真主链路

一次运行经过页面提交、权限核验、输入冻结、后台仿真和结果持久化。页面按运行编号轮询，再读取业务终态、事件和容量样本。

| 组件 | 入口 | 调用与职责 |
|---|---|---|
| 工作负载页面 | `apps/web/workload.html` | 选择输入、提交仿真、轮询、展示结果与只读解释 |
| Gateway | `services/gateway/src/gateway/api.py`、`main.py` | 注册路由并接入权限检查 |
| 访问控制 | `services/gateway/src/gateway/security.py` | viewer/editor 共享令牌和受限制的本地访问 |
| SimulationService | `services/gateway/src/gateway/simulation_api.py` | 校验并冻结输入、排队、调用 `simulate(spec)`、保存结果 |
| 场景分派 | `packages/fabric-sim/src/fabric_sim/__init__.py` | 按 DDP 或 TP 场景选择适配器 |
| 领域适配器 | 同目录 `ddp.py`、`tp.py` | 准入、进度、故障、资源释放与业务终态 |
| 公共内核 | 同目录 `core.py` | 事件调度、共享资源、通信流与容量计算 |
| SQLite | `simulations_v2` 表 | 将规范化输入、运行状态与结果绑定到同一 id |

当前服务使用一个后台执行线程和默认两个等待位。HTTP 提交与查询是异步的，多个仿真计算仍串行运行。队列满返回 429，输入或执行失败返回相应错误，不无限堆积请求。SQLite 保存证据记录，不是可自动续跑的持久任务队列。

### 数据与接口

| 对象 | 主要内容 | 保持的约束 |
|---|---|---|
| 仿真输入 | `scenario`、`topology`、`profile`、`jobs` 或 `replicas/requests`、`faults` | 校验、规范化、深拷贝并计算 `input_hash` |
| 运行记录 | `id`、`status`、输入快照、结果、时间、错误 | 查询返回同一次运行的输入和证据 |
| 运行结果 | `summary`、作业/请求结果、`events`、`resource_samples` | 汇总可以回到领域状态与容量记录 |
| 只读解释 | `observations[i].source`、假设和模型边界 | 引用现有结果，不改写配置或运行结果 |

`SimulationSpec`、`SimulationRun` 是接口契约概念，实际实现使用字典与 SQLite 记录。

| 接口 | 行为 |
|---|---|
| `GET /api/v2/scenarios` | 读取示例输入 |
| `POST /api/v2/simulations` | 返回 202、运行 id 与 `input_hash` |
| `GET /api/v2/simulations` | 查询近期运行 |
| `GET /api/v2/simulations/{id}` | 查询状态、原输入和结果 |
| `GET /api/v2/simulations/{id}/events` | 分页读取已生成结果中的事件 |
| `POST /api/v2/simulations/{id}/explain` | 生成带来源引用的只读规则解释 |

服务状态由 `queued` 到 `running`，随后进入 `completed` 或 `failed`。启动服务时，数据库中遗留的未完成记录标记为 `interrupted`。服务 `completed` 只表示仿真正常结束，各作业或请求仍有自己的成功、失败、等待或拒绝状态。`/events` 不提供实时 GPU 事件流。

### 网络模型

网络采用有效容量资源模型。同节点通信消耗有限机内总线；跨节点消耗两端 NIC；跨机架还消耗两端的方向上联。tx/rx 独立计容量。

对于路径上的每项资源，容量按活跃流数等分。一条流的速率取路径上各份额的最小值，即 `r_f = min(C_r / n_r)`。所有作业的活跃流统一计算，避免分组局部计算后重复占用同一容量。

该规则是保守可行分配：其他瓶颈留下的闲置份额不会再次分配。它不保证带宽充分利用或最优 max-min 公平。Ring 使用单桶、轮间屏障和无计算通信重叠的抽象，不复刻 NCCL 的分桶、算法选择和协议。系统没有逐包、RDMA/PFC/ECN 或详细 PCIe 仿真，也未实现任意拓扑多路径优化。

### DDP 与 TP 状态

| 条件 | DDP 仿真 | TP 仿真 |
|---|---|---|
| 准入 | 固定规模完整组，同时检查卡数、显存、所有权和连通性 | 固定 TP 规模副本、显存与 KV 预留 |
| 正常进度 | 计算、梯度同步、更新、检查点及下一步 | prefill、decode、token 与请求终态 |
| 成员故障 | 取消受损作业整组通信，释放 GPU，回滚已提交步 | 故障副本的在途请求失败，释放 KV |
| 未受损部分 | 健康作业继续，释放资源后可供等待作业恢复 | 健康副本继续处理请求 |
| 旧事件 | 执行 epoch 与 owner generation 阻止旧回调推进新状态 | 迟到回调不提交已经失效请求的 token |

仿真中的检查点是逻辑提交步和配置延时；真实文件保存、哈希校验与加载由独立 DDP 执行后端实现。

## 2 真实执行 Agent

`agent_demo` 中的 Controller 与领域 Adapter 位于同一监督进程。TCP 通道或 `torchrun` 训练在子进程中运行。模块分层不表示已部署成多个微服务。

| 阶段 | 接口或记录 | 执行守卫 |
|---|---|---|
| 观察与诊断 | `observe`、`diagnose`；Observation/Diagnosis | 诊断引用当前观测中的证据编号 |
| 生成与校验 | `propose`、`validate`；Candidate | 仅使用已登记候选 id，检查方案版本与后端约束 |
| 执行 | `apply`；Receipt | 幂等键绑定候选；只操作授权资源和登记进程 |
| 验证 | `verify`；Verification | 有待验证回执时只能继续验证；成功需有验证证据 |

候选对象的主键为 `id`，工具参数使用 `candidate_id`。`schema_version` 表示数据格式，`version` 表示方案版本，`epoch` 表示进程组执行代次，训练进度用 `step` 表示。

当前规则策略执行明确的工具顺序，并设定调用与尝试预算。`llm` 是可选决策接口，仍受同一校验和成功判据约束。平台 Web 的 Explain 是独立的只读规则解释，不依赖这一 LLM 接口。

### 真实 DDP 恢复

一次恢复先冻结已提交检查点 manifest 并核验文件哈希，再确认完整旧组停止，最后启动新的固定规模组并加载状态。GPU 位置保留时，对应旧 rank 也重启。恢复源包含模型、优化器、步数及各 rank 随机状态。

验证检查完整 rank 集合、完成状态、执行位置、实际 GPU 身份、恢复源及新训练进度。`started` 回执不足以证明恢复成功。无可行候选时返回 `blocked`，与仿真内等待资源释放的生命周期不同。

真实候选校验尚未重新检查动态显存、实时链路连通性或带宽，不能将仿真准入规则视为真实后端的完整实现。当前候选成本主要计算新引入 GPU 槽数，未将恢复后的通信性能纳入优化目标。

## 3 现场演示

`demos/live/server.py` 提供标准库 HTTP 服务，页面位于 `demos/live/web`。它包含三个模式：

- 网络方案评估调用随演示包保存的仿真内核副本，冻结输入、枚举候选并重新计算方案。
- TCP 模式创建当前会话拥有的进程，基于真实探测、传输结果和新 ACK/SHA 证据恢复。
- GPU 模式读取已导出的服务器记录，展示历史 DDP 恢复，不在浏览器所在电脑启动 GPU 训练。

演示服务与 v2 Gateway 不是同一个应用。演示使用独立 run/session 标识，阻止旧会话请求和迟到响应修改新会话。`vendor` 保留供演示独立运行的代码副本，并通过来源记录和仓库检查脚本核对。

## 4 权限与扩展边界

Web 的 viewer/editor 是共享角色令牌，尚无个人账户和项目级访问隔离。未配置令牌时使用受检查的本地访问模式。CLI 不自动继承 Web 权限，而由授权 GPU 池和登记进程身份限定操作对象。

当前仅支持单协调器，进程内锁、版本和幂等回执不等于跨重启事务保证。检查点保留会增加磁盘与 I/O 开销；两卡组合枚举为资源池规模的平方级。生产化仍需补齐动态资源复核、跨控制器事务对账、账户隔离和更多重复实验。

旧 `fabric-domain`、planner、aggregator、evaluator 与 `/legacy` 保留兼容用途。`infra` 的 NATS/PostgreSQL 设计不在 v2 仿真主链路中，不能据目录存在推断其已完成端到端集成。
