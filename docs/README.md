# 文档索引

项目的当前使用方式从下表四份文档开始。仓库同时保留早期静态规划器、分布式设计及 v2 重构过程的原始材料，便于追溯设计变化。

## 当前文档

| 文档 | 解决的问题 |
|---|---|
| [项目首页](../README.md) | 项目做什么，三类运行入口如何区分 |
| [快速开始](QUICKSTART.md) | 环境、启动、真实执行、测试、常见运行问题 |
| [系统架构](ARCHITECTURE.md) | 当前组件调用、数据契约、状态与权限边界 |
| [提交说明](SUBMISSION.md) | 代码拼合来源、验证范围、证据口径和交付检查 |
| [现场演示说明](../demos/live/README.md) | 笔记本演示、自定义拓扑、TCP 操作和证据回放 |
| [现场演示模型接口](../demos/live/MODEL_API.md) | 候选生成、冻结输入、仿真比较与适配规则 |

## 历史设计与实施记录

以下文件保留原位，原有相对链接可继续使用。它们记录的是特定阶段的设计、执行计划或验收结果，不应直接代替当前版本说明。

| 材料 | 时间背景与阅读方式 |
|---|---|
| [S1 领域契约](s1-domain-contracts.md) 至 [S10 故障重规划](s10-fault-replanning.md) | 早期静态规划与版本管理模块；包含当前兼容路径仍使用的基础代码 |
| [总体设计](architecture-design.md)、[分布式评估](s9-distributed-evaluation.md)、[基础设施](../infra/README.md) | 含目标架构和基础设施骨架；不能据此声称 v2 已使用分布式 worker/NATS 主链 |
| [v2 实现合同](refactor/IMPLEMENTATION_CONTRACT.md)、[重构交付记录](refactor/DELIVERY.md) | v2 仿真重构时的约定和当轮验收口径 |
| [Agent 执行手册](refactor/AGENT_EXECUTION.md)、[GPU 操作记录](refactor/GPU_EXECUTION.md) | 早期分工和执行安排；当前启动方法以快速开始为准 |
| [架构报告源稿](refactor/architecture_report.md)、[对应历史 PDF](refactor/architecture_report.pdf) | 保留阶段性课程报告；源稿与 PDF 可能不是同一次导出，报告事实需与当前代码、证据对照 |
| [评分要求映射](refactor/rubric_mapping.md)、[DDP 任务表](refactor/case1_tasks.md)、[TP 任务表](refactor/case2_tasks.md) | 课程规划与任务划分，不代表逐项完成证明或个人代码贡献 |
| [旧接口与助手说明](security-and-assistant.md)、[评审记录](review-audit-20261101.md) | 对应早期接口或审查时点，HTTP 状态码、持久化和 Assistant 状态须区分版本 |

公开实验记录中的个人源码位置、主机名和硬件标识已经匿名化；占位路径不是运行本仓库所需的固定路径。GPU 槽位、指标和时间戳仍保留原记录含义，见 [脱敏说明](PRIVACY.md)。旧记录中“未做 GPU 实测”的表述已被后续真实 DDP 实验补充，但后续实验并不自动验证早期文档的全部设计。

## 证据入口

- [GPU 案例](../evidence/gpu-case1/)：当次真实执行源码和导出结果。
- [仿真记录](../evidence/simulation/)：配置、结果、事件及容量对照。
- [早期证据](../evidence/legacy/)：保留旧模型与旧测量的独立口径。

引用结论时同时说明代码版本、输入配置、执行环境和结果来源。历史记录与本次仓库整理验证分别记录。
