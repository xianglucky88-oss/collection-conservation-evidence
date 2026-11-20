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
| `trap-activity` | 按连续布放已知的单次检查计算每诱捕盒日虫数。 | `examples/trap-activity.json` |
| `transport-shocks` | 按已去重力分量的同一采样时刻三轴加速度和给定触发线记录冲击样本。 | `examples/transport-shocks.json` |
| `material-coupons` | 按单个金属试片保留外观腐蚀等级。 | `examples/material-coupons.json` |
| `humidity-response` | 按试验轮次和柜内位置复核闭柜后的恢复时间，并按柜体布置汇总。 | `examples/humidity-response.json` |

活动口径来自各样本。样例记录的业务标识用作事实键或保留在结果中。`tests/expected` 中的结果只描述这些基线试样。合成数据和示例规则用于开发验证，实际签发、专业判定及合规认证需采用对应项目的正式数据与规则。

### `humidity-response`

试验单给出必检位置 `required_positions`、允许的记录间隔
`max_observation_gap_minutes`、连续合格需要维持的时长
`required_hold_minutes`，以及目标相对湿度范围 `lower_rh_pct` /
`upper_rh_pct`。每个轮次记录一条，带 `layout`（柜体布置）、`case`、
`observations`，并在再次开柜时给出 `reopened_at_minutes`。

恢复时间不是首个落在目标带的读数，而是某位置第一次进入一段**连续合格**
记录、且这段记录在记录上真正维持够要求时长的起点：

- 相邻记录的间隔超过允许值视为测点失联，失联前后的读数不会被拼成一段；
- 短暂进入目标带后又离开的读数只记入 `transient_in_band`，不算恢复；
- 测点失联（记录间隔超过允许值）前后的读数不会被拼成一段，也不能据此给出
  可证明的上限；
- 无法确认时，位置给出 `recovery_lower_bound_minutes`（最后一次观察到仍在
  带外的时间，恢复必在其后）与 `unconfirmed_in_band_since_minutes`（最后
  一段带内记录的起点，只是候选：仅当此后一直留在带内并维持够时长才成立，
  否则真正的恢复更晚）；
- 只有全部必检位置都确认，轮次才记 `complete`；否则记 `incomplete`，并列出
  `unconfirmed_required_positions`。再次开柜时，开柜后的读数被列入
  `excluded_observation_ids`，不参与判断；开柜前已完成的记
  `completed_before_reopening`，未完成的记 `incomplete_at_reopening`。

结果按事实类型区分：`fact_type` 为 `round` 的记录逐轮、逐位置列出上述证据；
`fact_type` 为 `layout` 的记录按柜体布置汇总多轮结果，给出
`slowest_confirmed_recovery_minutes` 与 `slowest_evidenced_positions`（有证据
的最慢位置）、`rounds_without_any_confirmed_recovery`（尚无可确认恢复的轮次）、
`recovery_floor_minutes`（无论补测结果如何都不会更早的下限），以及
`planning_ready` / `planning_recovery_minutes`：只有所有轮次都完成时，该布置
的恢复时间才可用于排布展计划，否则由 `planning_blocked_by_round_ids` 和
`planning_blocked_by_positions` 指出缺口。
