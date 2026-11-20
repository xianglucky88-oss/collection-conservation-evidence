# 馆藏预防性保护

将展陈、储藏、运输与保护试验的离线记录整理成可追溯证据。实验和监测数据来自合成样本。

数据工程子方向为**数据分析与数据应用**。基线接收已整理为 UTF-8 JSON 的合成业务数据，按数据链独立产出 JSON 事实。仓库根目录可直接运行，运行时和测试使用 Python 3.11 或以上的标准库。

## 运行

```sh
python3 -m collection_conservation_evidence light-exposure examples/light-exposure.json
python3 -m collection_conservation_evidence light-exposure examples/light-exposure.json --sqlite facts.sqlite
python3 -m collection_conservation_evidence light-dose-audit examples/light-dose-audit.json
python3 -m unittest discover -s tests -v
```

Python 调用入口为 `process(pipeline, data)`。命令行也可以将 JSON 结果输出到标准输出。选用 SQLite 时，`runs` 保留原始 JSON 及 SHA-256，`facts` 保留每项具名事实和对应批次。每个输入按规范化 JSON 的摘要标识，同一处理链重复导入同一输入只保留一批；输入或处理失败时不会留下半批结果。处理逻辑改版后的重算应使用独立结果库。

## 数据链与离线样本

| 数据链 | 基线行为 | 试样 |
|---|---|---|
| `light-exposure` | 计算每段恒定照度观测的光剂量。 | `examples/light-exposure.json` |
| `light-dose-audit` | 结合交接时间窗、探头适用位置与年度额度，核算单件藏品到指定日期已用剂量的可信下界/上界、暗库停留与无人测量空档，以及给定照度下可确认的剩余陈列时长。 | `examples/light-dose-audit.json` |
| `trap-activity` | 按连续布放已知的单次检查计算每诱捕盒日虫数。 | `examples/trap-activity.json` |
| `transport-shocks` | 按已去重力分量的同一采样时刻三轴加速度和给定触发线记录冲击样本。 | `examples/transport-shocks.json` |
| `material-coupons` | 按单个金属试片保留外观腐蚀等级。 | `examples/material-coupons.json` |
| `humidity-response` | 记录闭柜后单探头首次进入目标相对湿度范围的读数。 | `examples/humidity-response.json` |

活动口径来自各样本。样例记录的业务标识用作事实键或保留在结果中。`tests/expected` 中的结果只描述这些基线试样。合成数据和示例规则用于开发验证，实际签发、专业判定及合规认证需采用对应项目的正式数据与规则。

## `light-dose-audit` 核算口径

- **时间轴原子化**：以读数实际测量范围、交接窗口两端、年度边界和核算窗口边界切分时间轴；每段时间只归属一个确定位置或一个交接窗口，同一时间不重复归属。
- **探头适用位置**：读数只有在藏品处于探头所属位置的时段内才被采用；同一时段多探头重叠时取同时段最低/最高照度作为该段的可信照度区间。
- **更正读数**：`corrects` 指向的读数在更正记录与原记录的实际重叠范围内被替代（原读数被挖空，完全被替代时以 `superseded: true` 留痕）；更正范围超出原读数的部分不产生剂量。
- **暗库与空档分开**：暗库（`kind: dark`）内无人测量的时段按可证明的零照度计入 `dark_stays`；亮厅无人测量时段计入 `unmeasured_gaps`，下界为 0，上界取该位置 `max_lux`；缺少 `max_lux` 时上界为 `null` 且 `upper_bound_supported` 为假。
- **交接窗口**：窗口内藏品可能仍在原处或已到新处，下界取零（或窗口内实际测量），上界取两个候选亮厅 `max_lux` 的最大值；任一候选位置无上限依据则上界不可支持。
- **年度切分**：跨年展期在 1 月 1 日切开，各年度只承担该年内实际发生的剂量，并分别对照 `annual_quotas` 判断。
- **剩余陈列时长**：`guaranteed_remaining_hours =（年度额度 − 已用上界）/ query_lux`，上界不可支持时为 `null`；`best_effort_remaining_hours` 以已用下界估算。`warnings` 用 `confirmed_over_limit`、`possible_exhaustion`、`upper_bound_unsupported` 点名影响结论的具体空档段（`seg:nnn`）或交接。
