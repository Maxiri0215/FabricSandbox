# 本地仿真适配器接口

`demo_model.py` 使用 Python 3.10+ 标准库。`vendor/fabric_sim/` 是原项目的未修改副本；逐文件指纹在 `vendor/fabric_sim_source.json`。接口执行真实离散事件计算，参数是 **SYNTHETIC 合成参数**，不执行 GPU 训练或大模型推理。

## 默认入口输入（兼容原版本）

```python
from demo_model import plan_case, simulate_case
plan = plan_case({"uplink_gbps": 50, "memory_gib": 24,
                  "blocked_nodes": [], "steps": 30, "include_raw": False})
result = simulate_case({**plan["request"], "scenario": "ddp",
                        "variant": plan["selected_variant"]})
```

| 参数 | 默认值与允许范围 | 作用 |
|---|---|---|
| `uplink_gbps` | 50，数值 5–200 | 所有机架方向资源的容量；25/50/100 可以用于滑块 |
| `memory_gib` | 24，数值 0.1–128 | 所有 GPU 的显存；设为 1 展示单卡需求 4GiB 的拒绝依据 |
| `blocked_nodes` | `[]`，`n0`–`n3` 数组 | 仿真时刻 0 的节点失效；设为 `["n0"]` 时仅有 6 张健康卡，不能同时部署两个固定 4 卡组 |
| `steps` | 30，整数 1–30 | 每个 DDP 作业的目标步数；现场建议保留 30 |
| `include_raw` | false，布尔 | true 保留全部事件/容量样本；默认只缩减展示数据，所有指标仍由完整结果计算 |
| `scenario` | `ddp`，或 `tp` | 仅 `simulate_case` 使用；`plan_case` 固定比较双 DDP 作业 |
| `variant` | DDP 为 `distributed`，TP 为 `fault` | 仅 `simulate_case` 使用 |

DDP 支持 `baseline`（仅 A 的竞争对照）、`distributed`、`compact`、`compact_reverse`、原内核的 `competition`、`spread`、`admission`、`fault_no_spare`、`fault_spare`、`checkpoint`、`memory_reject`、`slow_rank`。TP 支持 `baseline`、`tp1`、`tp2`、`tp4`、`competition`、`kv_reject`、`fault`。不提供 `memory_gib` 时，`memory_reject`/`kv_reject` 保留原本的低显存设置；显式提供则覆盖。`steps` 不改变 TP 请求长度。

## 自定义拓扑接口

HTTP 服务新增以下接口，原 `/api/planning/analyze`、`/api/planning/apply` 与 Python 入口继续保留：

| 接口 | 请求与返回 |
|---|---|
| `GET /api/topology/default` | 返回默认两机架拓扑、`summary` 与 `topology_hash` |
| `POST /api/topology/validate` | 请求为纯拓扑对象或 `{ "topology": {...} }`；返回补齐缺省值的 `topology`、`summary: {racks, nodes, gpus}` 与 `topology_hash`，不执行仿真 |
| `POST /api/planning/analyze` | 原请求可新增 `topology`；服务端冻结输入，生成候选并执行仿真 |
| `POST /api/planning/apply` | 仍只需 `run_id` 和 `candidate_id`；候选必须通过约束且在评估时域内完成全部作业。按服务端冻结的拓扑重算，并检查候选输入哈希及负载哈希 |

POST 沿用 `Content-Type: application/json` 和 `X-Demo-Token`。令牌来自本地服务的 `GET /api/config`，无需新增账户。合法 JSON 但不满足拓扑格式的请求返回 HTTP 400，不影响原默认入口。

拓扑格式示例（3 机架、12 卡）：

```json
{
  "nodes": [
    {"id":"node-a0","rack_id":"rack-a","gpu_count":2,"memory_gib":24},
    {"id":"node-a1","rack_id":"rack-a","gpu_count":2,"memory_gib":24},
    {"id":"node-b0","rack_id":"rack-b","gpu_count":2,"memory_gib":24},
    {"id":"node-b1","rack_id":"rack-b","gpu_count":2,"memory_gib":24},
    {"id":"node-c0","rack_id":"rack-c","gpu_count":2,"memory_gib":24},
    {"id":"node-c1","rack_id":"rack-c","gpu_count":2,"memory_gib":24}
  ],
  "node_bandwidth_gbps":100,
  "uplink_bandwidth_gbps":50,
  "local_bandwidth_gbps":200,
  "link_latency_us":5,
  "local_latency_us":2
}
```

可直接使用随包提供的 `examples/topology_3racks.json`：

```python
import json
from pathlib import Path
from demo_model import plan_case, simulate_case, validate_topology

topology = json.loads(Path("examples/topology_3racks.json").read_text())
print(validate_topology(topology)["summary"])
plan = plan_case({"topology": topology, "steps": 30})
if plan["selected_variant"] is not None:
    result = simulate_case({**plan["request"], "variant": plan["selected_variant"]})
    assert result["input_hash"] == plan["selected_candidate"]["input_hash"]
```

自定义输入约定：

- `nodes` 必填，1–16 个节点；`id` 唯一，`rack_id` 相同即属于同机架。ID 只使用 ASCII 字母、数字、下划线或连字符，长度 1–48。未知字段、重复节点、非有限数值都会拒绝。
- `gpu_count` 缺省 2，整数 1–8，总 GPU 数至多 64；`memory_gib` 缺省 24，范围 0.01–1024。
- 三种带宽缺省分别为 100、50、200 Gbps，范围 0.001–100000；两种延迟缺省为 5、2 微秒，范围 0–1000000。每类带宽在全拓扑统一。
- 拓扑校验允许少于 8 卡，但当前双 4 卡作业分析需要至少 8 张物理卡。业务显存或健康资源不足仍返回有拒绝理由的 `blocked` 计划。
- 不能同时传 `topology` 与旧的 `uplink_gbps` / `memory_gib`，以免参数存在两个来源。可传 `steps`、`include_raw`，以及由当前节点 ID 组成的 `blocked_nodes`。
- 自定义分支支持 DDP 的 `distributed`、`compact`、`compact_reverse` 和仅 A 的 `baseline`。三个候选根据当前拓扑生成，不依赖 `n0`–`n3` 或 `r0`–`r1` 命名；`baseline` 只作竞争对照，不参加双作业选优。默认分支的 TP 和其他故障场景维持原行为。
- `topology`、负载、profile、故障及评估时域纳入冻结输入和哈希。应用请求中额外传入的拓扑不会覆盖已分析的输入。
- 评估窗口为 60000 ms。约束通过但未在窗口内完成的候选保留完成数量，整体 `makespan_ms` 为 `null`；不显示加速比，不推荐或允许采用该候选。可调整容量后重新分析。

仿真内核未修改。这里改变的是输入适配、候选生成与拓扑展示，仍为机架/节点两层资源模型，不支持任意边列表、物理交换机路由或逐条链路异构带宽。

## 计划输出

在本目录运行 `python3 build/generate_topology_fixtures.py` 可生成当前模型的响应样本；`build/topology_fixtures.json` 中的 `default` 是 50Gbps 计划输出。该文件按需生成，不作为历史实测证据。关键字段：

- `status`: `planned` 或 `blocked`。`selected_candidate_id`、`selected_variant`、`selected_candidate` 在 blocked 时均为 null。
- `input_hash`: 原始当前方案的完整规范输入哈希。`workload_hash` 排除 GPU 放置、放置策略及展示标签，其余字段保留，用来检验比较的负载、拓扑、故障与评估时域相同。
- `request`: 规范化参数；`spec`、`topology`、`jobs`、`placements` 是当前方案输入；GPU ID 为 `n0:gpu0` 格式。
- `candidates`: 默认入口有 9 个声明候选，每个作业从跨机架分散、机架 r0、机架 r1 中选一项；自定义拓扑入口有 3 个确定性候选。两者都不是全局搜索。默认可行的 id/variant 为 `distributed`、`compact`、`compact_reverse`。其余 `spread_r0` 等标识只用于展示拒绝原因，不能提交给 `simulate_case`。
- 每个候选含 `id`、`variant`、`label`、`placements`、`admissible`、`accepted`、`reasons`、`checks`、`moved_gpu_assignments`、`metrics`、`input_hash`、`workload_hash`。`accepted` 与 `admissible` 相同，只表示约束通过；是否选中看 `selected_candidate_id`。拒绝项的 metrics/hash 为 null。约束检查包含完整通信组、单卡显存、节点健康、Ring 连通、GPU 独占、同时部署卡数。
- `comparison`: 对象，默认键为 `baseline`、`distributed`、`compact`、`compact_reverse`。在没有可行候选时仅有 baseline；不要假设所有键永远存在。
- `comparison_contract`: 明确保持相同与变化的字段；A 单作业 baseline 是竞争对照，不参加双作业优选。
- `selection_rule`: 最小仿真 makespan，其次较少移动 GPU 分配数，再按 id 确定性破同分。选中项必须在仿真时域内完成全部作业。
- `diagnosis.evidence`: 一条原始机架上行容量样本；`source` 指向 `result.resource_samples`。
- `result`: 当前分散方案的原仿真结果（含 `jobs` 状态轨迹、恢复记录、`invariant_checks`、`engine_stats`、`events`、`resource_samples` 等）。顶层 summary/events/resource_samples/raw_counts 为便捷别名。
- `provenance`: 明确合成参数、仿真非 GPU 实测、原内核 hash、新增枚举适配器边界。

候选格式示例：

```json
{"id":"compact","variant":"compact","admissible":true,
 "placements":{"A":["n0:gpu0","n0:gpu1","n1:gpu0","n1:gpu1"],
               "B":["n2:gpu0","n2:gpu1","n3:gpu0","n3:gpu1"]},
 "reasons":[],"metrics":{"makespan_ms":5343.733235200019}}
```

## 单场景输出

`simulate_case` 返回 `schema_version`、`scenario`、`variant`、`input_hash`、`workload_hash`、`spec`、`topology`、`jobs`、`placements`、`provenance`、`result`。`summary`、`metrics`、`events`、`resource_samples`、`raw_counts`、`display_sampling` 也有顶层别名。

`result.jobs` 是 DDP 完整作业输出，包含状态时间线与恢复证据；TP 则看 `result.requests`、`result.replicas`。`result.summary.failed` 不能因为后续请求成功而隐藏。默认保留全部业务事件；传输/集合通信高频明细仅抽取初始 96 条至 `result.representative_transfers`。容量样本每个资源至多 66 点，保留首尾、均匀点和峰值。`raw_counts` 是原始条数，`display_counts` 是展示条数。需全部源事件时使用 include_raw。

## 边界与使用约定

规划与应用是两次独立仿真。服务端应冻结 plan 的输入并保留计划 ID，应用时按缓存参数和选中 variant 重算，校验 input_hash 与候选一致。这里没有原运行任务的跨进程在线迁移，因此界面应称为“应用方案并重新仿真”，不能把两次独立轨迹冒充一个持续运行的 GPU 作业。真实本地 TCP 故障恢复由独立执行器提供；GPU 实测证据也应保留独立来源标识。

原内核 `fault_no_spare` 是一个连续的 DDP 故障恢复仿真，可展示 checkpoint 回滚、等待完整健康资源、恢复开销和后续提交步；TP `fault` 展示中断请求失败、KV 丢弃、模型重载、后续请求成功与旧回调隔离。

在本目录使用 Python 3.10+ 运行测试：`python3 -m unittest discover -s . -p test_demo_model.py -v`。其他拓扑与 HTTP handler 检查见本目录 `README.md`。
