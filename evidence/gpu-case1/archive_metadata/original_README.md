# Case 1 证据包导航

本包只整理已完成的 GPU 实验，未重跑或修改训练/恢复逻辑。

1. [主事件完整故事](main_incident_story.md)：同一个 incident 的输入→故障→诊断→候选→选择→执行→验证。
2. [时间线](timeline.csv) / [候选表](candidates.csv)：单位、epoch、版本及原始证据引用齐全。
3. [实验总表](experiment_overview.csv)：四组正式 GPT-2 + 单列 tiny 冒烟，空值不补 0。
4. [导出说明](export_notes.md)：环境、历史完整命令、代码修复、指标定义、限制与缺失。
5. [原矩阵离线入口](gpu_matrix_01/index.html) / [主事件离线报告](gpu_matrix_01/gpt2_recover/report.html)。
6. source_executed/ 是服务器矩阵实际使用代码；source_current/ 为导出时源码；history/ 和 server_run/code_changes.diff 保留历史修改。
7. previous_A0_A5/ 为旧原始实测；reused_A1_01/ 为旧 A1 导入后的仿真。不要混为本次 DDP。

主事件 incident-20361f4c7c3942d3bd13e11d7da1dfbb，worker run_id b8169b2eba6c45578365d4e91879779b。实际恢复源是冻结 e0/s5，不是最新 e1/s20。

ground_truth.json 仅用于事后核对；诊断基于 worker 退出、心跳和进度。权重全部留在服务器，zip 不含 .pt。文件可用 SHA256SUMS 校验。
