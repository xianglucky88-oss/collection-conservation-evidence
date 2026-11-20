"""把 material-effects 事实渲染为中文复核报告（Markdown）。

用法::

    python3 -I scripts/render_material_effects_report.py examples/material-effects.json > reports/material-effects-review.md

脚本只读取传入的 JSON 并调用包内处理函数，不执行输入中的任何内容。
"""

from __future__ import annotations

import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from collection_conservation_evidence import process  # noqa: E402


STATUS_LABELS = {
    "control_available": "对照有效",
    "no_control": "无有效对照",
    "evaluated": "已判定",
    "insufficient_records": "记录不足",
    "no_valid_control": "无有效对照",
    "pending_insufficient_records": "待核（记录不足）",
    "pending_no_valid_control": "待核（无有效对照）",
    "pending_no_matched_combination": "待核（缺匹配组合）",
    "pending_matched_combination_insufficient_records": "待核（匹配组合记录不足）",
}

DIRECTION_LABELS = {
    "increase": "上升",
    "decrease": "下降",
    "no_change": "无变化",
    "undetermined": "未定",
}

REASON_LABELS = {
    "missing_blank_control": "缺空白对照",
    "single_blank_control": "仅剩一只空白对照，需重复",
    "missing_matched_combination": "缺匹配组合",
    "insufficient_matched_combination_records": "匹配组合记录不足",
}

CHANGE_LABELS = {
    "direction": "方向改变",
    "status": "状态改变",
    "status_and_direction": "状态与方向均改变",
}

TARGET_LABELS = {
    "control": "空白对照",
    "combination": "组合总体",
    "material": "材料单独影响",
}


def fmt(value):
    if value is None:
        return "—"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def cell(value):
    return fmt(value).replace("|", "\\|")


def status(value):
    return STATUS_LABELS.get(value, fmt(value))


def direction(value):
    return DIRECTION_LABELS.get(value, fmt(value))


def reason_label(value):
    return REASON_LABELS.get(value, fmt(value))


def readable_affects(affects):
    result = []
    for entry in affects:
        kind, _, target = entry.partition(":")
        if kind == "control":
            result.append(f"空白对照（{target}）")
        elif kind == "combination":
            combo, metal = target.rsplit("/", 1)
            result.append(f"{combo} 组合的 {metal} 方向")
        elif kind == "material":
            combo, material, metal = target.rsplit("/", 2)
            result.append(f"{combo} 组合中 {material} 的单独影响（{metal}）")
        else:
            result.append(target)
    return "；".join(result)


def detail_label(value):
    if value.startswith("no valid blank control"):
        return "该金属在该暴露期没有有效空白对照"
    if value.startswith("only one valid blank control"):
        return "该金属在该暴露期只剩一只有效空白对照容器"
    if value.startswith("no ") and value.endswith("combination in the same exposure period"):
        combo = value[3:].removesuffix("-only combination in the same exposure period")
        return f"同一暴露期内没有只放 {combo} 的组合"
    if value.endswith("combination has no comparable pre/post records"):
        combo = value.removesuffix("-only combination has no comparable pre/post records")
        return f"只放 {combo} 的组合缺少可比的暴露前后记录"
    return value


def join_materials(materials):
    return " + ".join(materials) if materials else "（空白）"


def scenario_label(fact):
    kind = fact["scenario_kind"]
    if kind == "baseline":
        return "基线"
    if kind == "add_pre_grade":
        return f"情景 {fact['scenario']}：补齐暴露前记录"
    if kind == "invalidate_control":
        return f"情景 {fact['scenario']}：认定对照容器交叉污染"
    return fact["scenario"]


def table(header, rows):
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join(["---"] * len(header)) + "|"]
    lines.extend("| " + " | ".join(row) + " |" for row in rows)
    return "\n".join(lines)


def by_scope(facts, scope):
    return [fact for fact in facts if fact["scope"] == scope]


def render_controls(facts):
    rows = []
    for fact in by_scope(facts, "control"):
        rows.append(
            [
                fact["exposure_period"],
                fact["metal"],
                status(fact["status"]),
                str(fact["container_count"]),
                str(fact["coupon_count"]),
                str(fact["valid_record_count"]),
                cell(fact["median_change"]),
                ", ".join(fact["container_ids"]) or "—",
                ", ".join(fact["invalidated_container_ids"]) or "—",
            ]
        )
    return table(
        ["暴露期", "金属", "对照状态", "独立容器", "试片", "可比记录", "对照中位变化", "有效容器", "本次排除容器"],
        rows,
    )


def render_combinations(facts):
    rows = []
    for fact in by_scope(facts, "combination"):
        rows.append(
            [
                fact["exposure_period"],
                fact["metal"],
                join_materials(fact["combination"]),
                f"{fact['valid_container_count']}/{fact['container_count']}",
                f"{fact['valid_record_count']}/{fact['coupon_count']}",
                cell(fact["treatment_median_change"]),
                cell(fact["control_median_change"]),
                cell(fact["relative_change"]),
                direction(fact["direction"]),
                status(fact["status"]),
                str(fact["comparable_above_visual_limit_count"]),
                str(fact["excluded_above_visual_limit_count"]),
            ]
        )
    return table(
        [
            "暴露期", "金属", "组合", "有效容器/容器", "可比记录/试片",
            "处理中位变化", "对照中位变化", "相对空白", "方向", "状态",
            "可比超限片", "已排除超限片",
        ],
        rows,
    )


def render_materials(facts):
    rows = []
    for fact in by_scope(facts, "material"):
        if fact["status"] == "evaluated":
            conclusion = f"{direction(fact['direction'])}（{fmt(fact['relative_change'])} 级）"
        else:
            conclusion = "待核"
        basis = "空白对照" if fact["basis"] == "blank_control" else "匹配组合"
        comparison = join_materials(fact["comparison_combination"]) if fact["basis"] == "matched_combination" else "空白"
        rows.append(
            [
                fact["exposure_period"],
                fact["metal"],
                join_materials(fact["combination"]),
                fact["material"],
                basis,
                comparison,
                cell(fact["comparison_median_change"]),
                cell(fact["relative_change"]),
                conclusion,
                status(fact["status"]),
            ]
        )
    return table(
        ["暴露期", "金属", "组合", "材料", "判据", "对照/比较", "比较中位变化", "相对变化", "结论", "状态"],
        rows,
    )


def render_recommendations(facts):
    rows = []
    for fact in by_scope(facts, "recommendation"):
        target = join_materials(fact["materials"])
        if fact["reason"] == "missing_blank_control":
            target = "空白对照"
        elif fact["reason"] == "single_blank_control":
            target = "额外空白对照（重复）"
        rows.append(
            [
                str(fact["rank"]),
                fact["exposure_period"],
                target,
                ", ".join(fact["metals"]),
                reason_label(fact["reason"]),
                detail_label(fact["detail"]),
                readable_affects(fact["affects"]),
            ]
        )
    return table(["优先级", "暴露期", "建议组合", "涉及金属", "原因", "说明", "解除哪些待核"], rows)


def render_impacts(facts):
    rows = []
    for fact in by_scope(facts, "amendment_impact"):
        target = (
            f"{join_materials(fact['combination'])} / {fact['material']}"
            if fact["target_scope"] == "material"
            else join_materials(fact["combination"])
        )
        rows.append(
            [
                TARGET_LABELS.get(fact["target_scope"], fact["target_scope"]),
                CHANGE_LABELS.get(fact["change"], fact["change"]),
                fact["exposure_period"],
                fact["metal"],
                target,
                f"{status(fact['before']['status'])} / {direction(fact['before']['direction'])} / {cell(fact['before']['relative_change'])}",
                f"{status(fact['after']['status'])} / {direction(fact['after']['direction'])} / {cell(fact['after']['relative_change'])}",
            ]
        )
    return table(
        ["对象", "改变类型", "暴露期", "金属", "组合/材料", "修订前（状态/方向/相对变化）", "修订后（状态/方向/相对变化）"],
        rows,
    )


def render_scenario(facts, scenario, amendment_note=None):
    subset = [fact for fact in facts if fact["scenario"] == scenario]
    head = scenario_label(subset[0])
    lines = [f"### {head}", ""]
    if amendment_note:
        lines.append(f"**修订内容：** {amendment_note}")
        lines.append("")
    impacts = by_scope(subset, "amendment_impact")
    if impacts:
        lines.append("**结论改变：**")
        lines.append("")
        lines.append(render_impacts(subset))
        lines.append("")
    else:
        lines.append("**结论改变：** 无状态或方向变化。")
        lines.append("")
    combos = by_scope(subset, "combination")
    if combos:
        lines.append("**本情景的组合总体判断：**")
        lines.append("")
        rows = [
            [
                fact["exposure_period"],
                fact["metal"],
                join_materials(fact["combination"]),
                cell(fact["treatment_median_change"]),
                cell(fact["control_median_change"]),
                direction(fact["direction"]),
                cell(fact["relative_change"]),
                status(fact["status"]),
            ]
            for fact in combos
        ]
        lines.append(
            table(
                ["暴露期", "金属", "组合", "处理中位变化", "对照中位变化", "方向", "相对空白", "状态"],
                rows,
            )
        )
        lines.append("")
    pending_material = [
        fact for fact in subset
        if fact["scope"] == "material" and fact["status"] != "evaluated"
    ]
    if pending_material:
        lines.append("**仍待核的材料单独影响：**")
        lines.append("")
        rows = [
            [
                fact["exposure_period"],
                fact["metal"],
                join_materials(fact["combination"]),
                fact["material"],
                status(fact["status"]),
            ]
            for fact in pending_material
        ]
        lines.append(table(["暴露期", "金属", "组合", "材料", "状态"], rows))
        lines.append("")
    recommendations = by_scope(subset, "recommendation")
    if recommendations:
        lines.append("**本情景下最值得补做的对照组合：**")
        lines.append("")
        lines.append(render_recommendations(subset))
        lines.append("")
    if not impacts and not combos and not pending_material:
        lines.append("本情景没有可报方向或待核项。")
        lines.append("")
    return "\n".join(lines)


def render_summary(source, facts):
    baseline = [fact for fact in facts if fact["scenario"] == "baseline"]
    lines = ["## 0. 结论摘要", ""]

    caveats = []
    for fact in baseline:
        if fact["scope"] == "combination" and fact["excluded_above_visual_limit_count"]:
            caveats.append(
                f"{fact['exposure_period']} · {fact['metal']} · "
                f"{join_materials(fact['combination'])}：有 "
                f"{fact['excluded_above_visual_limit_count']} 枚外观超限片因入箱前已有斑"
                f"或记录不可比而被排除，超限等级不能直接归因于材料。"
            )
    invalidated = sorted(
        {
            container
            for amendment in source.get("amendments", [])
            if amendment.get("kind") == "invalidate_control"
            for container in [amendment["container"]]
        }
    )
    if invalidated:
        caveats.append(
            f"空白对照 {', '.join(invalidated)} 已认定与待评材料共用暴露环境；"
            "基线中所有用到该对照的方向都需按修订情景重看。"
        )
    for caveat in caveats:
        lines.append(f"- {caveat}")
    if caveats:
        lines.append("")

    evaluated = [
        fact for fact in baseline
        if fact["scope"] == "combination" and fact["status"] == "evaluated"
    ]
    if evaluated:
        lines.append("基线可报方向的组合（均为“相对空白”，不是材料单独效应）：")
        lines.append("")
        for fact in evaluated:
            lines.append(
                f"- {fact['exposure_period']} · {fact['metal']} · "
                f"{join_materials(fact['combination'])}：{direction(fact['direction'])}"
                f"（{fmt(fact['relative_change'])} 级，对照 "
                f"{fact['control_container_count']} 只容器）"
            )
        lines.append("")

    pending = [
        fact for fact in baseline
        if fact["scope"] == "material" and fact["status"] != "evaluated"
    ]
    lines.append(
        f"材料单独影响共 {len(pending)} 项仍待核，"
        "主要缺口是同一暴露期内缺少只放单一材料的匹配组合。"
    )
    lines.append("")
    recs = by_scope(baseline, "recommendation")[:3]
    if recs:
        lines.append("下一次优先补做：")
        lines.append("")
        for fact in recs:
            target = join_materials(fact["materials"]) or "空白对照"
            lines.append(
                f"- {fact['exposure_period']}：只放 {target} 的组合"
                f"（涉及 {', '.join(fact['metals'])}；{reason_label(fact['reason'])}）"
            )
        lines.append("")
    return "\n".join(lines)


def build_report(source):
    facts = process("material-effects", source)
    baseline = [fact for fact in facts if fact["scenario"] == "baseline"]
    scenarios = []
    for fact in facts:
        if fact["scenario"] != "baseline" and fact["scenario"] not in scenarios:
            scenarios.append(fact["scenario"])

    lines = ["# 材料效应复核报告（合成试验数据）", ""]
    lines.append(
        "本报告由 `material-effects` 数据链根据试验分组、暴露期和评级口径生成。"
        "输入为合成数据，结论仅用于方法验证，不替代正式签发判定。"
    )
    lines.append("")
    lines.append(render_summary(source, facts))
    lines.append("")
    lines.append("## 1. 复核口径")
    lines.append("")
    lines.append(
        "- 同一容器的金属片共享一次暴露环境：先取容器内各试片变化的中位数，"
        "再在容器之间取中位数；独立证据单元按容器计，不按试片计。"
    )
    lines.append(
        "- 只有同一金属、同一暴露期、具备可比的暴露前后等级，并且存在有效空白对照时，"
        "才报告相对空白的变化方向。"
    )
    lines.append(
        "- 入箱前已有斑的试片不进入变化计算；缺失暴露前记录的试片先除外，补齐后重算。"
    )
    lines.append(
        "- 空白对照只支持“组合总体相对空白”的判断。组合中某一种材料的单独影响，"
        "须由只差该材料的匹配组合支持；没有匹配组合时留为待核。"
    )
    lines.append("")
    lines.append(f"输入共 {len(source['containers'])} 只容器、{len(source['coupons'])} 枚试片，"
                 f"评级超限线为 {source['max_grade']} 级，方向判定阈值为 "
                 f"{source.get('min_change_grades', 1)} 级。")
    lines.append("")
    lines.append("## 2. 基线复核")
    lines.append("")
    lines.append("### 2.1 空白对照有效性")
    lines.append("")
    lines.append(render_controls(baseline))
    lines.append("")
    lines.append("### 2.2 组合总体相对空白的变化")
    lines.append("")
    lines.append(render_combinations(baseline))
    lines.append("")
    lines.append("### 2.3 材料单独影响")
    lines.append("")
    lines.append(render_materials(baseline))
    lines.append("")
    lines.append("### 2.4 下一次最值得补做的对照组合")
    lines.append("")
    lines.append(render_recommendations(baseline))
    lines.append("")
    lines.append("## 3. 修订情景")
    lines.append("")
    notes = {
        amendment["id"]: amendment.get("note") or ""
        for amendment in source.get("amendments", [])
    }
    for scenario in scenarios:
        lines.append(render_scenario(facts, scenario, notes.get(scenario)))
        lines.append("")

    lines.append("## 4. 使用限制")
    lines.append("")
    lines.append(
        "- 报告只给出等级变化方向，不推断腐蚀机理或材料相容性结论。"
    )
    lines.append(
        "- 一只容器内的多枚试片不是独立重复；容器数不足时，组合结论的稳健性有限。"
    )
    lines.append(
        "- 修订情景逐项单独应用，互不叠加；若两项修订同时成立，应合并输入后重算。"
    )
    lines.append(
        "- “结论改变”只列状态或方向发生变化的条目；仅相对变化数值变化、"
        "方向不变的条目不列为结论改变，可在各情景明细表核对。"
    )
    lines.append("")
    return "\n".join(lines)


def main(argv):
    if len(argv) != 2:
        raise SystemExit("usage: render_material_effects_report.py INPUT.json")
    source = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
    sys.stdout.write(build_report(source))


if __name__ == "__main__":
    main(sys.argv)
