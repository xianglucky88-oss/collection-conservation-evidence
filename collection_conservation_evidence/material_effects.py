"""按试验分组、暴露期和评级口径复核衬材与胶的材料效应。

输入结构（UTF-8 JSON）::

    {
      "max_grade": 1,
      "min_change_grades": 1,
      "containers": [
        {"id": "P1-B0", "role": "control", "materials": [],
         "exposure_period": "2026-04-01/2026-06-30"}
      ],
      "coupons": [
        {"id": "P1-B0-s1", "container": "P1-B0", "metal": "silver",
         "pre_grade": 0, "post_grade": 0,
         "pre_existing_blemish": false}
      ],
      "amendments": [
        {"id": "A1", "kind": "add_pre_grade", "coupon": "P2-L1-s3",
         "pre_grade": 0, "note": "补充入箱前照片"},
        {"id": "A2", "kind": "invalidate_control", "container": "P2-B0",
         "note": "与待评组合共用一次暴露环境"}
      ]
    }

复核口径：

* 同一容器内的试片共享一次暴露环境。容器内先取各试片变化的中位数，
  再在容器之间取中位数；一只容器只算一个独立证据单元。
* 只有同一金属、同一暴露期、同时具备暴露前后等级，并且存在有效空白对照，
  才报告“相对空白”的变化方向。材料的匹配组合比较同样要求该金属在该暴露期
  有有效空白对照；否则只记为待核。
* 空白对照只能说明整个组合相对空白是否变化。要判断组合中某一种材料的
  单独影响，必须有只差该材料的匹配组合；没有时留为待核。
* 入箱前已有斑的试片不进入变化计算。缺失暴露前记录的试片先除外，
  补齐记录后重新计算。
* 修订情景逐项应用，互不叠加：补齐一份暴露前记录，或认定一只对照容器
  交叉污染。报告列出结论变化和下一次最值得补做的对照组合。
"""

from __future__ import annotations

from collections import defaultdict
from statistics import median

from .contract import Row, integer, read_rows, text


def _optional_grade(row: Row, field: str) -> int | None:
    value = row.get(field)
    if value is None:
        return None
    if type(value) is not int or value < 0:
        raise ValueError(f"{field} must be an integer of at least 0 or null")
    return value


def _optional_flag(row: Row, field: str) -> bool:
    value = row.get(field, False)
    if type(value) is not bool:
        raise ValueError(f"{field} must be true or false")
    return value


def _optional_note(row: Row) -> str | None:
    value = row.get("note")
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError("note must be nonempty text when present")
    return value


def _safe_text(row: Row, field: str, forbidden: str) -> str:
    value = text(row, field)
    if any(char in value for char in forbidden):
        raise ValueError(f"{field} must not contain any of: {forbidden}")
    return value


def _text_list(row: Row, field: str) -> tuple[str, ...]:
    value = row.get(field, [])
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise ValueError(f"{field} must be a list of nonempty text")
    if len(set(value)) != len(value):
        raise ValueError(f"{field} must not contain duplicates")
    for item in value:
        if any(char in item for char in "|+"):
            raise ValueError(f"{field} entries must not contain '|' or '+'")
    return tuple(sorted(value))


def _index(rows: list[Row], label: str) -> dict[str, Row]:
    result = {}
    for row in rows:
        key = text(row, "id")
        if "|" in key:
            raise ValueError(f"{label} id must not contain '|': {key}")
        if key in result:
            raise ValueError(f"duplicate {label} id: {key}")
        result[key] = row
    return result


def _optional_rows(data: Row, field: str) -> list[Row]:
    if field not in data:
        return []
    rows = data[field]
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError(f"{field} must contain JSON objects")
    return rows


def _normalise(data: Row) -> Row:
    max_grade = integer(data, "max_grade")
    min_change = data.get("min_change_grades", 1)
    if type(min_change) is not int or min_change < 1:
        raise ValueError("min_change_grades must be an integer of at least 1")

    containers: dict[str, Row] = {}
    for key, row in _index(read_rows(data, "containers"), "container").items():
        role = text(row, "role")
        if role not in ("control", "treatment"):
            raise ValueError("role must be control or treatment")
        materials = _text_list(row, "materials")
        if role == "control" and materials:
            raise ValueError(f"control container {key} must not list materials")
        if role == "treatment" and not materials:
            raise ValueError(f"treatment container {key} must list materials")
        containers[key] = {
            "id": key,
            "role": role,
            "materials": materials,
            "exposure_period": _safe_text(row, "exposure_period", "|"),
        }

    coupons: dict[str, Row] = {}
    used_containers: set[str] = set()
    for key, row in _index(read_rows(data, "coupons"), "coupon").items():
        container_id = text(row, "container")
        if container_id not in containers:
            raise ValueError(f"coupon {key} references unknown container: {container_id}")
        used_containers.add(container_id)
        coupons[key] = {
            "id": key,
            "container": container_id,
            "metal": _safe_text(row, "metal", "|+"),
            "pre_grade": _optional_grade(row, "pre_grade"),
            "post_grade": integer(row, "post_grade"),
            "pre_existing_blemish": _optional_flag(row, "pre_existing_blemish"),
        }
    empty = sorted(set(containers) - used_containers)
    if empty:
        raise ValueError(f"containers without coupons: {', '.join(empty)}")

    amendments: dict[str, Row] = {}
    amended_coupons: set[str] = set()
    amended_containers: set[str] = set()
    for key, row in _index(_optional_rows(data, "amendments"), "amendment").items():
        kind = text(row, "kind")
        if kind == "add_pre_grade":
            coupon = text(row, "coupon")
            if coupon not in coupons:
                raise ValueError(f"amendment {key} references unknown coupon: {coupon}")
            if coupons[coupon]["pre_grade"] is not None:
                raise ValueError(f"coupon {coupon} already has a pre_grade record")
            if coupon in amended_coupons:
                raise ValueError(f"more than one add_pre_grade amendment for coupon {coupon}")
            amended_coupons.add(coupon)
            amendments[key] = {
                "id": key,
                "kind": kind,
                "coupon": coupon,
                "pre_grade": integer(row, "pre_grade"),
                "note": _optional_note(row),
            }
        elif kind == "invalidate_control":
            container = text(row, "container")
            if container not in containers:
                raise ValueError(f"amendment {key} references unknown container: {container}")
            if containers[container]["role"] != "control":
                raise ValueError(f"amendment {key} must reference a control container")
            if container in amended_containers:
                raise ValueError(f"more than one invalidate_control amendment for container {container}")
            amended_containers.add(container)
            amendments[key] = {
                "id": key,
                "kind": kind,
                "container": container,
                "note": _optional_note(row),
            }
        else:
            raise ValueError(f"unknown amendment kind: {kind}")

    return {
        "max_grade": max_grade,
        "min_change_grades": min_change,
        "containers": containers,
        "coupons": coupons,
        "amendments": amendments,
    }


def _rounded(value: float | None) -> float | None:
    return None if value is None else round(value, 3)


def _direction(value: float, threshold: int) -> str:
    if value >= threshold:
        return "increase"
    if value <= -threshold:
        return "decrease"
    return "no_change"


def _change(coupon: Row, pre_grade: int | None) -> tuple[int | None, str | None]:
    if coupon["pre_existing_blemish"]:
        return None, "pre_existing_blemish"
    if pre_grade is None:
        return None, "missing_pre_grade"
    return coupon["post_grade"] - pre_grade, None


def _container_evidence(trial: Row, pre_overrides: dict[str, int]) -> dict[tuple, Row]:
    evidence: dict[tuple[str, str, str], Row] = {}
    for coupon in trial["coupons"].values():
        container = trial["containers"][coupon["container"]]
        key = (container["exposure_period"], coupon["metal"], container["id"])
        item = evidence.setdefault(
            key,
            {
                "exposure_period": container["exposure_period"],
                "metal": coupon["metal"],
                "container": container["id"],
                "role": container["role"],
                "materials": container["materials"],
                "changes": [],
                "excluded": [],
                "coupons": [],
            },
        )
        pre_grade = pre_overrides.get(coupon["id"], coupon["pre_grade"])
        change, reason = _change(coupon, pre_grade)
        record = {
            "coupon": coupon["id"],
            "pre_grade": pre_grade,
            "post_grade": coupon["post_grade"],
            "change": change,
        }
        item["coupons"].append(record)
        if change is None:
            item["excluded"].append({**record, "reason": reason})
        else:
            item["changes"].append(change)
    for item in evidence.values():
        item["coupons"].sort(key=lambda row: row["coupon"])
        item["excluded"].sort(key=lambda row: row["coupon"])
        item["median_change"] = _rounded(median(item["changes"])) if item["changes"] else None
        item["included_coupon_count"] = len(item["changes"])
        item["coupon_count"] = len(item["coupons"])
    return evidence


def _container_detail(item: Row) -> Row:
    return {
        "container": item["container"],
        "median_change": item["median_change"],
        "included_coupon_count": item["included_coupon_count"],
        "coupon_count": item["coupon_count"],
        "coupons": item["coupons"],
    }


def _facts_for_scenario(
    trial: Row,
    scenario: str,
    kind: str,
    amendment: str | None,
    note: str | None,
    invalid_controls: set[str],
    pre_overrides: dict[str, int],
) -> tuple[list[Row], dict[tuple, Row]]:
    evidence = _container_evidence(trial, pre_overrides)
    containers = trial["containers"]
    max_grade = trial["max_grade"]
    threshold = trial["min_change_grades"]

    meta = {"scenario": scenario, "scenario_kind": kind, "amendment": amendment}
    if note is not None:
        meta["note"] = note

    # Control summaries: one fact per exposure period and metal.
    control_items: dict[tuple[str, str], list[Row]] = defaultdict(list)
    for item in evidence.values():
        if item["role"] != "control" or item["container"] in invalid_controls:
            continue
        control_items[(item["exposure_period"], item["metal"])].append(item)

    periods_metals = sorted({(item["exposure_period"], item["metal"]) for item in evidence.values()})
    control_summary: dict[tuple[str, str], Row] = {}
    control_facts: list[Row] = []
    for period, metal in periods_metals:
        items = sorted(control_items.get((period, metal), []), key=lambda item: item["container"])
        medians = [item["median_change"] for item in items if item["median_change"] is not None]
        median_change = _rounded(median(medians)) if medians else None
        valid_ids = [item["container"] for item in items if item["median_change"] is not None]
        invalidated_ids = sorted(
            container_id
            for container_id in invalid_controls
            if (period, metal, container_id) in evidence
        )
        excluded = [
            {"container": item["container"], **row}
            for item in items
            for row in item["excluded"]
        ]
        included_coupons = [
            coupon for item in items for coupon in item["coupons"] if coupon["change"] is not None
        ]
        summary = {
            "container_count": len(valid_ids),
            "coupon_count": sum(item["coupon_count"] for item in items),
            "valid_record_count": sum(item["included_coupon_count"] for item in items),
            "median_change": median_change,
            "comparable_above_visual_limit_count": sum(
                1 for coupon in included_coupons if coupon["post_grade"] > max_grade
            ),
            "excluded_above_visual_limit_count": sum(
                1 for coupon in excluded if coupon["post_grade"] > max_grade
            ),
            "container_ids": valid_ids,
            "invalidated_container_ids": invalidated_ids,
            "container_details": [_container_detail(item) for item in items],
            "excluded_coupons": sorted(excluded, key=lambda row: (row["container"], row["coupon"])),
        }
        control_summary[(period, metal)] = summary
        control_facts.append(
            {
                "fact_id": f"{scenario}|control|{period}|{metal}",
                **meta,
                "scope": "control",
                "exposure_period": period,
                "metal": metal,
                "status": "control_available" if valid_ids else "no_control",
                **summary,
            }
        )

    # Treatment combinations: group independent containers by period, materials and metal.
    combo_containers: dict[tuple[str, tuple[str, ...], str], dict[str, Row]] = defaultdict(dict)
    for (period, metal, container_id), item in evidence.items():
        if item["role"] != "treatment":
            continue
        combo_containers[(period, item["materials"], metal)][container_id] = item

    combo_summary: dict[tuple, Row] = {}
    combination_facts: list[Row] = []
    for (period, materials, metal), items in sorted(combo_containers.items()):
        ordered = [items[key] for key in sorted(items)]
        medians = [item["median_change"] for item in ordered if item["median_change"] is not None]
        treatment_median = _rounded(median(medians)) if medians else None
        control = control_summary[(period, metal)]
        control_median = control["median_change"]
        relative = (
            _rounded(treatment_median - control_median)
            if treatment_median is not None and control_median is not None
            else None
        )
        if treatment_median is None:
            status = "insufficient_records"
        elif control_median is None:
            status = "no_valid_control"
        else:
            status = "evaluated"
        direction = (
            _direction(relative, threshold)
            if status == "evaluated" and relative is not None
            else "undetermined"
        )
        included_coupons = [
            coupon for item in ordered for coupon in item["coupons"] if coupon["change"] is not None
        ]
        excluded = [
            {"container": item["container"], **row}
            for item in ordered
            for row in item["excluded"]
        ]
        summary = {
            "combination": list(materials),
            "container_count": len(ordered),
            "valid_container_count": len(medians),
            "coupon_count": sum(item["coupon_count"] for item in ordered),
            "valid_record_count": sum(item["included_coupon_count"] for item in ordered),
            "treatment_median_change": treatment_median,
            "control_median_change": control_median,
            "control_container_count": control["container_count"],
            "relative_change": relative,
            "direction": direction,
            "status": status,
            "comparable_above_visual_limit_count": sum(
                1 for coupon in included_coupons if coupon["post_grade"] > max_grade
            ),
            "excluded_above_visual_limit_count": sum(
                1 for coupon in excluded if coupon["post_grade"] > max_grade
            ),
            "container_ids": sorted(items),
            "container_details": [_container_detail(item) for item in ordered],
            "excluded_coupons": sorted(excluded, key=lambda row: (row["container"], row["coupon"])),
        }
        combo_summary[(period, materials, metal)] = summary
        combination_facts.append(
            {
                "fact_id": f"{scenario}|combination|{period}|{metal}|{'+'.join(materials)}",
                **meta,
                "scope": "combination",
                "exposure_period": period,
                "metal": metal,
                **summary,
            }
        )

    # Material-specific effects: blank control for a single material,
    # matched combination otherwise.
    material_facts: list[Row] = []
    for (period, materials, metal), summary in sorted(combo_summary.items()):
        for material in materials:
            fact_id = f"{scenario}|material|{period}|{metal}|{'+'.join(materials)}|{material}"
            base = {
                "combination": list(materials),
                "material": material,
                "metal": metal,
                "exposure_period": period,
            }
            if len(materials) == 1:
                status = {
                    "evaluated": "evaluated",
                    "insufficient_records": "pending_insufficient_records",
                    "no_valid_control": "pending_no_valid_control",
                }[summary["status"]]
                direction = summary["direction"] if status == "evaluated" else "undetermined"
                fact = {
                    "fact_id": fact_id,
                    **meta,
                    "scope": "material",
                    **base,
                    "basis": "blank_control",
                    "comparison_combination": [],
                    "comparison_median_change": summary["control_median_change"],
                    "relative_change": summary["relative_change"],
                    "direction": direction,
                    "status": status,
                    "reason": None if status == "evaluated" else status,
                    "control_container_count": summary["control_container_count"],
                }
            else:
                matched = tuple(sorted(item for item in materials if item != material))
                matched_summary = combo_summary.get((period, matched, metal))
                comparison = None
                relative = None
                if summary["control_median_change"] is None:
                    status = "pending_no_valid_control"
                    reason = "no valid blank control for this metal and exposure period"
                elif matched_summary is None:
                    status = "pending_no_matched_combination"
                    reason = "no matched combination in the same exposure period"
                elif matched_summary["treatment_median_change"] is None:
                    status = "pending_matched_combination_insufficient_records"
                    reason = "matched combination has no comparable pre/post records"
                else:
                    status = "evaluated"
                    reason = None
                    comparison = matched_summary["treatment_median_change"]
                    relative = _rounded(summary["treatment_median_change"] - comparison)
                fact = {
                    "fact_id": fact_id,
                    **meta,
                    "scope": "material",
                    **base,
                    "basis": "matched_combination",
                    "comparison_combination": list(matched),
                    "comparison_median_change": comparison,
                    "relative_change": relative,
                    "direction": (
                        _direction(relative, threshold)
                        if status == "evaluated"
                        else "undetermined"
                    ),
                    "status": status,
                    "reason": reason,
                    "control_container_count": summary["control_container_count"],
                }
            material_facts.append(fact)

    # Next control combinations worth adding.
    recommendations: dict[tuple, Row] = {}
    treated_period_metals = {(period, metal) for period, _, metal in combo_summary}
    for (period, metal), control in control_summary.items():
        if (period, metal) not in treated_period_metals:
            continue
        if control["median_change"] is None:
            key = ("missing_blank_control", period, ())
            detail = "no valid blank control for this metal and exposure period"
        elif control["container_count"] == 1:
            key = ("single_blank_control", period, ())
            detail = "only one valid blank control container remains; repeat for replication"
        else:
            continue
        item = recommendations.setdefault(
            key,
            {"exposure_period": period, "materials": [], "metals": [], "reason": key[0],
             "detail": detail, "affects": [], "score": 0},
        )
        item["metals"].append(metal)
        item["affects"].append(f"control:{metal}")
        item["score"] += 1
    for (period, materials, metal), summary in combo_summary.items():
        if summary["status"] == "no_valid_control":
            key = ("missing_blank_control", period, ())
            item = recommendations.setdefault(
                key,
                {"exposure_period": period, "materials": [], "metals": [], "reason": key[0],
                 "detail": "no valid blank control for this metal and exposure period",
                 "affects": [], "score": 0},
            )
            item["metals"].append(metal)
            item["affects"].append(f"combination:{'+'.join(materials)}/{metal}")
            item["score"] += 1
        if len(materials) == 1:
            continue
        for material in materials:
            matched = tuple(sorted(item for item in materials if item != material))
            matched_summary = combo_summary.get((period, matched, metal))
            if matched_summary is not None and matched_summary["treatment_median_change"] is not None:
                continue
            if matched_summary is None:
                reason = "missing_matched_combination"
                detail = f"no {'+'.join(matched)}-only combination in the same exposure period"
            else:
                reason = "insufficient_matched_combination_records"
                detail = f"{'+'.join(matched)}-only combination has no comparable pre/post records"
            key = (reason, period, matched)
            item = recommendations.setdefault(
                key,
                {"exposure_period": period, "materials": list(matched), "metals": [],
                 "reason": reason, "detail": detail, "affects": [], "score": 0},
            )
            item["metals"].append(metal)
            item["affects"].append(f"material:{'+'.join(materials)}/{material}/{metal}")
            item["score"] += 1

    recommendation_facts = []
    for rank, row in enumerate(
        sorted(
            recommendations.values(),
            key=lambda row: (-row["score"], row["exposure_period"], row["materials"], row["reason"]),
        ),
        start=1,
    ):
        recommendation_facts.append(
            {
                "fact_id": f"{scenario}|recommendation|{rank:02d}",
                **meta,
                "scope": "recommendation",
                "rank": rank,
                "exposure_period": row["exposure_period"],
                "materials": row["materials"],
                "metals": sorted(set(row["metals"])),
                "reason": row["reason"],
                "detail": row["detail"],
                "affects": sorted(set(row["affects"])),
                "score": row["score"],
            }
        )

    facts = [*control_facts, *combination_facts, *material_facts, *recommendation_facts]
    conclusions = {
        (
            fact["scope"],
            fact["exposure_period"],
            fact["metal"],
            tuple(fact.get("combination", [])),
            fact.get("material"),
        ): fact
        for fact in [*control_facts, *combination_facts, *material_facts]
    }
    return facts, conclusions


def _impact_facts(
    baseline: dict[tuple, Row],
    after: dict[tuple, Row],
    scenario: str,
    kind: str,
    amendment: str,
    note: str | None,
) -> list[Row]:
    impacts: list[Row] = []
    for key, before in sorted(baseline.items(), key=lambda pair: repr(pair[0])):
        if key[0] == "control":
            continue
        changed = after.get(key)
        if changed is None:
            continue
        # Only status or direction changes count as a changed conclusion;
        # a numeric shift that keeps the same direction is not reported here.
        if before["status"] == changed["status"] and before["direction"] == changed["direction"]:
            continue
        if before["status"] != changed["status"] and before["direction"] != changed["direction"]:
            change = "status_and_direction"
        elif before["status"] != changed["status"]:
            change = "status"
        else:
            change = "direction"
        suffix = "|".join(
            part
            for part in (
                key[0],
                key[1],
                key[2],
                "+".join(key[3]) if key[3] else None,
                key[4],
            )
            if part is not None
        )
        facts = {
            "fact_id": f"{scenario}|impact|{suffix}",
            "scenario": scenario,
            "scenario_kind": kind,
            "amendment": amendment,
            "scope": "amendment_impact",
            "change": change,
            "target_scope": key[0],
            "exposure_period": key[1],
            "metal": key[2],
            "combination": list(key[3]),
            "material": key[4],
            "before": {
                "status": before["status"],
                "direction": before["direction"],
                "relative_change": before.get("relative_change"),
            },
            "after": {
                "status": changed["status"],
                "direction": changed["direction"],
                "relative_change": changed.get("relative_change"),
            },
        }
        if note is not None:
            facts["note"] = note
        impacts.append(facts)
    return impacts


def build_facts(data: Row) -> list[Row]:
    trial = _normalise(data)
    facts, baseline = _facts_for_scenario(
        trial, "baseline", "baseline", None, None, set(), {}
    )
    for amendment_id, amendment in trial["amendments"].items():
        invalid_controls: set[str] = set()
        pre_overrides: dict[str, int] = {}
        if amendment["kind"] == "add_pre_grade":
            pre_overrides[amendment["coupon"]] = amendment["pre_grade"]
        else:
            invalid_controls.add(amendment["container"])
        scenario_facts, conclusions = _facts_for_scenario(
            trial,
            amendment_id,
            amendment["kind"],
            amendment_id,
            amendment["note"],
            invalid_controls,
            pre_overrides,
        )
        facts.extend(scenario_facts)
        facts.extend(
            _impact_facts(
                baseline,
                conclusions,
                amendment_id,
                amendment["kind"],
                amendment_id,
                amendment["note"],
            )
        )
    return facts
