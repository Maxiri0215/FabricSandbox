# 汇报数据与复现证据

此目录按证据来源分为三类，保留PPT所用结果和必要版本标识。公开副本已对个人目录、主机和硬件标识脱敏，见 [公开副本标记](public_release.json) 和 [脱敏说明](../docs/PRIVACY.md)。整理与脱敏没有重新运行GPU、TCP或仿真实验。

| 内容 | 入口 | PPT对应结论与口径 |
|---|---|---|
| 真实GPU恢复 | [gpu-case1](gpu-case1/README.md)、[四组对照入口](gpu-case1/gpu_matrix_01/index.html) | GPT-2 Small两rank，GPU2/3失效后用2/4重建，从step5恢复至20；四组各一次，tiny另计 |
| v2离散事件仿真 | [simulation](simulation/README.md)、[19场景汇总](simulation/summary.json) | 共享上联竞争、紧凑放置、完整组恢复、TP请求终态；全部SYNTHETIC |
| 旧静态CPU模型性能 | [legacy](legacy/README.md)、[完整计时记录](legacy/06_benchmark.json) | 16至1024 GPU模型资源、128卡通信关系数量影响CPU评估耗时；不是GPU或v2内核规模实测 |

## 关键结论定位

| PPT中使用的数据/事实 | 可核对文件 |
|---|---|
| A独占约7.137s；A/B共享约10.721s；两个作业紧凑放置约5.344s | [仿真汇总](simulation/summary.json)、[竞争对照](simulation/contention_comparison.csv)，以及D2_A_alone、D2_competition、D1_compact目录的input与metrics |
| 仿真DDP无备用时等待健康作业释放资源；检查点提交中断回滚 | [D4_fault_no_spare](simulation/D4_fault_no_spare/result_summary.json)、[D4_checkpoint_fault](simulation/D4_checkpoint_fault/result_summary.json) |
| TP故障4请求中3完成、1失败，拦截1次旧回调 | [T3_fault结果摘录](simulation/T3_fault/result_summary.json) |
| GPT-2健康完成20、关闭修复停7、恢复完成20、无授权备用blocked | [真实实验总表](gpu-case1/experiment_overview.csv)、[矩阵](gpu-case1/gpu_matrix_01/matrix.json) |
| GPU2/3换为2/4；恢复源e0/s5；新step6、step8、step20；11项验证 | [主事件故事](gpu-case1/main_incident_story.md)、[候选](gpu-case1/candidates.csv)、[恢复原结果](gpu-case1/gpu_matrix_01/gpt2_recover/result.json) |
| 检测70.765ms；worker退出至两rank首恢复步12.552s；故障至终态94.940s | [时间线](gpu-case1/timeline.csv)、[服务器总结](gpu-case1/server_run/server_execution_summary.md)；94.940s含后续训练与验证，不能称纯MTTR |
| 原测量环境、GPT-2 profile与独立通信基准 | [旧A0–A5](gpu-case1/previous_A0_A5/)、[GPU运行环境](gpu-case1/server_run/environment.json)；旧profile为seq512，本次真实DDP为seq128 |
| 1024卡资源池下放置1.7373ms、评估0.3323ms；128卡全对全约5.62倍 | [拓扑规模CSV](legacy/ppt19_topology.csv)、[通信关系CSV](legacy/ppt20_workload.csv)，仅采用20261102_005258批次 |

## 源码与证据版本

[GPU实际执行源码](gpu-case1/source_executed/) 保持原字节，其中全部18个文件匹配 [source_final_manifest.json](gpu-case1/server_run/source_final_manifest.json)。正式矩阵在保留全部已提交checkpoint的修复之后执行，见 [code_changes.diff](gpu-case1/server_run/code_changes.diff)。后续工作只增加测试及更新说明，记录见 [final_status.json](gpu-case1/history/post_gpu_cpu_work/final_status.json)。

仿真输入、profile、checks、metrics未因身份脱敏而改变；`result_summary.json`为明确标注的结果摘录，省略流级events和resource_samples两个大数组，保留其余字段、原结果SHA、输入哈希及实现标识。原始完整结果仍保存在仓库之外。真实GPU的JSON、JSONL、日志和报告中的个人路径、主机和硬件标识使用一致的匿名值，其文件字节及摘要可能与原始导出不同；训练数值和事件顺序保持不变。检查点权重文件未随原包导出。

## 校验与边界

当前公开副本的完整文件清单是 [evidence_manifest.json](evidence_manifest.json)，路径均相对本目录，包含脱敏后的文件大小和SHA256，清单自身不纳入校验。复制来源和摘录规则见 [archive_selection.json](archive_selection.json)。GPU子目录内的原SHA清单与导出验收记录仍属于历史全包，可能列出本精选仓库没有复制的文件，也可能记录脱敏前摘要；不能把它们当作当前公开副本的全量校验值。检查点摘要仍指未随包提供的原始权重。

公开日志中的服务器/本机路径为占位路径，不要求接收者具有这些路径。匿名设备标识保留跨记录对应关系，不能用于识别原设备。矩阵HTML通过相对路径导航且不依赖外部资源，可下载仓库后离线打开。真实DDP只验证单机进程故障恢复，TCP现场演示另在项目demo目录；两者不能冒充跨主机、物理GPU、交换机或RDMA故障验证。模型完成时间与CPU wall time使用不同口径，不跨类拼接比较。
