# 馆藏预防性保护

将展陈、储藏、运输与保护试验的离线记录整理成可追溯证据。实验和监测数据来自合成样本。

数据工程子方向为**数据分析与数据应用**。基线接收已整理为 UTF-8 JSON 的合成业务数据，按数据链独立产出 JSON 事实。仓库根目录可直接运行，运行时和测试使用 Python 3.11 或以上的标准库。

## 运行

```sh
python3 -m collection_conservation_evidence light-exposure examples/light-exposure.json
python3 -m collection_conservation_evidence light-exposure examples/light-exposure.json --sqlite facts.sqlite
python3 -m unittest discover -s tests -v
```

Python 调用入口为 `process(pipeline, data)`。命令行也可以将 JSON 结果输出到标准输出。选用 SQLite 时，`runs` 保留原始 JSON 及 SHA-256，`facts` 保留每项具名事实和对应批次。每个输入按规范化 JSON 的摘要标识，同一处理链重复导入同一输入只保留一批；输入或处理失败时不会留下半批结果。处理逻辑改版后的重算应使用独立结果库。

## 数据链与离线样本

| 数据链 | 基线行为 | 试样 |
|---|---|---|
| `light-exposure` | 计算每段恒定照度观测的光剂量。 | `examples/light-exposure.json` |
| `trap-activity` | 按区域与有效观察期汇总诱捕盒活动量，处理提前撤走、换区交接、属级鉴定与补录。 | `examples/trap-activity.json` |
| `transport-shocks` | 按已去重力分量的同一采样时刻三轴加速度和给定触发线记录冲击样本。 | `examples/transport-shocks.json` |
| `material-coupons` | 按单个金属试片保留外观腐蚀等级。 | `examples/material-coupons.json` |
| `humidity-response` | 记录闭柜后单探头首次进入目标相对湿度范围的读数。 | `examples/humidity-response.json` |

### 诱捕活动量口径

`trap-activity` 的输入包含 `zones`、`traps`（带时间区间的 `placements`，相邻布放首尾相接，换区即交接）与 `checks`（带观察区间和计数条目 `entries`）。输出事实分五类：

- `trap-deployment:*`：每盒的实际布放时长、分区布放段与覆盖的检查，区域结论可由此查回纳入的盒。
- `trap-check:*`：每次检查的有效观察期、全部计数条目及其 `source`；补录条目以 `amends` 指向原条目，唯一未被补录的条目为准，计数只进入一次。
- `handover-batch:*`：检查区间跨两个区域且计数未分开清点时，整批虫数只能归属一个区域；两区各得 `0..批次数` 的互斥范围，观察盒日仍按交接时刻切分，两区不会同时计入整批。
- `zone-activity:*`：区域活动量，区分 `deployed_trap_days`（实际布放盒日）与 `effective_trap_days`（有效观察盒日），并分别列出 `zero_catch_checks`（有效观察中捕获为零）与 `unobserved_segments`（布放但未取得观察的时段）；计数给确定值与最小/最大可能值及每盒日速率。
- `genus-comparison:*` / `species-comparison:*`：属级事实汇总该属全部鉴定深度的记录；种级事实只收种级计数，并列出 `genus_rank_checks`（只鉴定到属、无法确认该物种是否出现的检查）与 `ambiguous_handover_checks`（交接归属未定的检查），`diagnosis_complete` 标明种级判定是否完整。
- `zone-comparison:*`：按每盒日速率的可能区间给出区域比较结论；区间重叠时为不可判定，没有有效观察的区域不参与排名。

活动口径来自各样本。样例记录的业务标识用作事实键或保留在结果中。`tests/expected` 中的结果只描述这些基线试样。合成数据和示例规则用于开发验证，实际签发、专业判定及合规认证需采用对应项目的正式数据与规则。
