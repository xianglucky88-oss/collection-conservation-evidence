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
| `material-effects` | 按试验分组、暴露期和评级口径复核衬材与胶的材料效应。 | `examples/material-effects.json` |
| `humidity-response` | 记录闭柜后单探头首次进入目标相对湿度范围的读数。 | `examples/humidity-response.json` |

活动口径来自各样本。样例记录的业务标识用作事实键或保留在结果中。`tests/expected` 中的结果只描述这些基线试样。合成数据和示例规则用于开发验证，实际签发、专业判定及合规认证需采用对应项目的正式数据与规则。

`material-effects` 的复核口径：

- 同一容器的金属片共享一次暴露环境。先取容器内各试片变化的中位数，再在容器之间取中位数；独立证据单元按容器计，一只容器只算一次。
- 只有同一金属、同一暴露期、具备可比的暴露前后等级，并且存在有效空白对照时，才报告相对空白的变化方向；否则为“未定/待核”。
- 入箱前已有斑的试片（`pre_existing_blemish`）不进入变化计算；缺失暴露前记录的试片先除外。
- 空白对照只支持“组合总体相对空白”的判断。组合中某一种材料的单独影响，须由只差该材料的匹配组合支持；没有匹配组合时留为待核。
- `amendments` 逐项单独应用（互不叠加）：`add_pre_grade` 补一份暴露前记录，`invalidate_control` 认定一只对照容器交叉污染。每个情景都重新计算，并输出 `amendment_impact` 事实列出结论改变；`recommendation` 事实按影响面排序给出下一次最值得补做的对照组合。

报告渲染：

```sh
python3 -I scripts/render_material_effects_report.py examples/material-effects.json > reports/material-effects-review.md
```
