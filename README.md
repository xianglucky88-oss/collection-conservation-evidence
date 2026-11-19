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
| `humidity-response` | 记录闭柜后单探头首次进入目标相对湿度范围的读数。 | `examples/humidity-response.json` |

活动口径来自各样本。样例记录的业务标识用作事实键或保留在结果中。`tests/expected` 中的结果只描述这些基线试样。合成数据和示例规则用于开发验证，实际签发、专业判定及合规认证需采用对应项目的正式数据与规则。
