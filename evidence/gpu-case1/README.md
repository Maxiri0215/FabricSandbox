# 真实 DDP 实验精选证据

> 公开副本：个人路径、主机及设备标识已匿名化。下文的原始导出、字节一致性及 SHA 记录描述当时采集阶段；当前文件以外层 [evidence_manifest.json](../evidence_manifest.json) 校验，见 [脱敏说明](../../docs/PRIVACY.md)。训练数值和事件顺序未改写。

本目录保留 2026-11-03 服务器单机双卡 GPT-2 Small 故障恢复实验的原始文件。它是原导出包的精选快照，不是重新执行的实验。

- [四组对照及 tiny 冒烟离线入口](gpu_matrix_01/index.html)
- [主恢复事件](main_incident_story.md)、[实验总表](experiment_overview.csv)、[候选](candidates.csv)、[时间线](timeline.csv)
- [服务器执行总结](server_run/server_execution_summary.md)、[实际执行源码](source_executed/)、[执行源码哈希](server_run/source_final_manifest.json)
- [保留检查点的历史修复](server_run/code_changes.diff)、[后续文档与测试变更记录](history/post_gpu_cpu_work/final_status.json)
- [旧 A0–A5 实测](previous_A0_A5/)、[旧 A1 导入仿真的小型报告](reused_A1_01/report.html)

正式四组均为随机初始化 GPT-2 Small、合成 token、FP32、seq128、world_size=2、目标20步。恢复组由GPU2/3改为2/4，从已提交step5恢复，最终完成step20。tiny为独立冒烟，不能计为第五组GPT-2结果。每组只有一次运行。

## 精选范围和校验

`source_executed/` 与 `gpu_matrix_01/` 保持原字节及完整子目录结构。选取过程没有修改原实验结果。`.pt` 权重原本就不在本地导出包内，记录中的服务器路径与SHA表示当时的文件验证结果，不能据此在当前电脑加载权重。

省略了重复的 `source_current/`、修复前源码副本、生成/打包脚本及大体积 `reused_A1_01/*_result.json`。保留的原始导出说明、索引、验收JSON和SHA清单描述原始完整导出包，因而可能列出当前精选目录未包含的文件；它们是历史记录，不能用来宣称此目录拥有原全包的每个文件。`archive_metadata/original_README.md` 是原导航原文，其相对链接须按原包根目录解释。

当前精选目录的文件完整性以 [上一级 evidence_manifest.json](../evidence_manifest.json) 为准。复制来源及派生规则见 [archive_selection.json](../archive_selection.json)。

## 适用边界

真实实验验证单机DDP worker进程退出、固定两rank完整组恢复与检查点一致性，没有验证GPU硬件损坏、跨服务器网络、RDMA、真实Qwen TP或外部LLM决策。旧A1 profile使用seq512，本次DDP使用seq128，两者时间不能直接比较。
