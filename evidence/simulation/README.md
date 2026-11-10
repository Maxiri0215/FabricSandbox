# v2 合成参数仿真证据

此目录对应原 `output/evidence/v2/revalidated`，保留19个场景的输入、profile、检查、逐作业/请求指标与小型结果摘录。没有在此次整理中重新运行仿真。

[summary.json](summary.json) 保留19场景的原始汇总及输入哈希；[contention_comparison.csv](contention_comparison.csv) 是独占/竞争对照。

每个场景的 `result_summary.json` 保留原 `result.json` 除 `events`、`resource_samples` 外的字段。`_archive` 标注原文件SHA256、原始大小、被省略字段，表示摘录而非完整结果文件。`input.json`、`profile.json`、`checks.json`、`metrics.csv` 为原字节复制。`metrics.csv` 内包含业务时间线，因此无需完整流事件也能检查作业或请求状态。

全部结果使用SYNTHETIC参数，不是GPU测量。复现入口是项目根目录 [scripts/run_v2_experiments.py](../../scripts/run_v2_experiments.py)；重新运行的CPU wall time可能变化，模拟时间由固定输入和对应内核决定。实现标识在各 `result_summary.json` 中。

重点场景：`D2_A_alone`为A独占，`D2_competition`为A/B分散且争用上联，`D1_compact`为两作业各在一个机架；`D4_fault_no_spare`检查等待其他作业释放资源后恢复；`D4_checkpoint_fault`检查保存中断只能回到上一已提交步；`T3_fault`检查请求失败、KV释放与迟到回调。
