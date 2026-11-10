# 旧静态模型 CPU 性能记录

数据来自 `kit/results/20261102_005258/01_model`，与PPT的数值一致。没有采用 `verified_results/model` 或09:21/09:24重跑批次。

- [ppt19_topology.csv](ppt19_topology.csv)：资源池16至1024 GPU，固定作业申请16 GPU；放置中位数0.0183至1.7373 ms，评估0.0387至0.3323 ms。
- [ppt20_workload.csv](ppt20_workload.csv)：128卡作业环形通信1.1261 ms，全对全6.3249 ms，约5.62倍。
- [06_benchmark.json](06_benchmark.json)：原始样本、环境、计时方法与源码摘要。
- [manifest.json](manifest.json)：当时采集范围和文件哈希；其绝对路径是历史来源。
- 主计时入口 [scripts/benchmark_review.py](../../scripts/benchmark_review.py) 和三项关键依赖与原记录相同，核查见 [source_verification.json](source_verification.json)。
- [source/run_model_evidence.py](source/run_model_evidence.py) 为当次证据采集脚本原字节副本，使用显式 `--project`、`--out` 参数定位工程和新输出。

计时为macOS arm64、Python3.12.14，预热1次、测量20次的CPU模型计算。它不含HTTP、数据库或GPU通信，也不是v2动态仿真内核的1024卡扩展测试。原报告中的速度不能直接用于训练性能结论。
