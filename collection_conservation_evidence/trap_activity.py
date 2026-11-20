"""按区域与有效观察期汇总诱捕盒活动量。

输入用统一的 day 坐标描述一周内的布放、逐盒检查与补录：

- ``traps[*].placements`` 给出布放区间 ``[start_day, end_day)`` 与所在区域；同一
  诱捕盒的相邻布放必须首尾相接，换区即一次交接。
- ``checks[*]`` 是一次检查覆盖的观察区间，``entries`` 是该区间的计数条目。补录条目
  用 ``amends`` 指向被补录的原始条目；以唯一未被补录的条目为准，每个计数只进入
  一次，全部条目连同 ``source`` 保留在事实中可核对。
- 检查区间跨两个区域且计数无法分开清点（计数为正）时形成交接批次：整批虫数只能
  归属其中一个区域，两区得到互斥的可能计数范围；观察盒日仍按交接时刻切分。
- ``rank`` 为 ``genus`` 时只鉴定到属。属级事实汇总该属及属下种级记录；物种级事实
  只收种级计数，并列出同属、无法确认该物种是否出现的属级检查。
- 有效观察中计数为 0 的检查（捕获为零）与布放期内没有取得观察的时段分别保留，
  互不混淆。
"""

from __future__ import annotations

from copy import deepcopy

from .contract import Row, index_rows, integer, number, read_rows, text

GENUS = "genus"
SPECIES = "species"


def _optional_text(row: Row, field: str) -> str | None:
    if field not in row or row[field] is None:
        return None
    value = row[field]
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be nonempty text")
    return value


def _required_text(row: Row, field: str) -> str:
    if field not in row:
        raise ValueError(f"missing required field: {field}")
    return text(row, field)


def _required_number(row: Row, field: str) -> float:
    if field not in row:
        raise ValueError(f"missing required field: {field}")
    return number(row, field, minimum=0)


def _required_integer(row: Row, field: str) -> int:
    if field not in row:
        raise ValueError(f"missing required field: {field}")
    return integer(row, field)


def _day(row: Row, field: str) -> float:
    return _required_number(row, field)


def _round6(value: float) -> float:
    return round(value, 6)


def build_facts(data: Row) -> list[Row]:
    # 装载过程会使用内部字段重写行，先深拷贝以保持调用方输入不可变
    data = deepcopy(data)
    zones = _load_zones(read_rows(data, "zones"))
    traps = _load_traps(read_rows(data, "traps"), zones)
    checks, entries = _load_checks(read_rows(data, "checks"), traps)

    facts: list[Row] = []
    stats = {zone: _new_stats() for zone in zones}

    for trap_id, trap in traps.items():
        facts.append(_deployment_fact(trap_id, trap, checks))
        for segment in _unobserved_segments(trap, checks):
            zone, start, end = segment
            stats[zone]["deployed_days"] += end - start
            stats[zone]["unobserved"].append(
                {"trap": trap_id, "start_day": _round6(start), "end_day": _round6(end),
                 "days": _round6(end - start)}
            )
        for placement in trap["placements"]:
            stats[placement["zone"]]["traps"].add(trap_id)
            stats[placement["zone"]]["deployed_days"] += placement["days"]

    batches: list[Row] = []
    for check in checks.values():
        fact, batch = _check_fact(check, traps[check["trap"]], stats, entries)
        facts.append(fact)
        if batch is not None:
            batches.append(batch)
            facts.append(batch)

    species_universe = _species_universe(checks, entries)
    facts.extend(_zone_activity_facts(zones, stats))
    facts.extend(_genus_facts(zones, stats))
    facts.extend(_species_facts(zones, stats, species_universe))
    facts.extend(_zone_comparison_facts(zones, stats))
    return facts


# ---------------------------------------------------------------- 输入校验与装载

def _load_zones(rows: list[Row]) -> dict[str, Row]:
    zones = index_rows(rows, "id")
    for zone_id, row in zones.items():
        row["name"] = _optional_text(row, "name") or zone_id
    return zones


def _load_traps(rows: list[Row], zones: dict[str, Row]) -> dict[str, Row]:
    traps = index_rows(rows, "id")
    for trap_id, row in traps.items():
        placements = read_rows(row, "placements")
        if not placements:
            raise ValueError(f"trap {trap_id} needs at least one placement")
        loaded = []
        for placement in placements:
            zone = _required_text(placement, "zone")
            if zone not in zones:
                raise ValueError(f"trap {trap_id} references unknown zone {zone}")
            start, end = _day(placement, "start_day"), _day(placement, "end_day")
            if end <= start:
                raise ValueError("placement end_day must be greater than start_day")
            loaded.append({"zone": zone, "start": start, "end": end, "days": end - start})
        loaded.sort(key=lambda item: item["start"])
        for earlier, later in zip(loaded, loaded[1:]):
            if later["start"] < earlier["end"] or later["end"] <= earlier["end"]:
                raise ValueError(f"trap {trap_id} placements overlap")
            if later["start"] != earlier["end"]:
                raise ValueError(f"trap {trap_id} placements must be contiguous")
        row["placements"] = loaded
    return traps


def _load_checks(rows: list[Row], traps: dict[str, Row]) -> tuple[dict[str, Row], dict[str, Row]]:
    checks = index_rows(rows, "id")
    entries: dict[str, Row] = {}
    for check_id, row in checks.items():
        trap_id = _required_text(row, "trap")
        if trap_id not in traps:
            raise ValueError(f"check {check_id} references unknown trap {trap_id}")
        start, end = _day(row, "start_day"), _day(row, "end_day")
        if end <= start:
            raise ValueError("check end_day must be greater than start_day")
        trap = traps[trap_id]
        if start < trap["placements"][0]["start"] or end > trap["placements"][-1]["end"]:
            raise ValueError(f"check {check_id} falls outside {trap_id} deployment")
        entry_rows = read_rows(row, "entries")
        if not entry_rows:
            raise ValueError(f"check {check_id} needs at least one entry")
        loaded_entries = []
        identity = None
        for entry_row in entry_rows:
            entry = _load_entry(entry_row, check_id, entries)
            if identity is None:
                identity = (entry["rank"], entry["genus"], entry["species"])
            elif (entry["rank"], entry["genus"], entry["species"]) != identity:
                raise ValueError(f"check {check_id} entries disagree on identification")
            loaded_entries.append(entry)
        amended = {entry["amends"] for entry in loaded_entries if entry["amends"] is not None}
        entry_ids = {entry["id"] for entry in loaded_entries}
        if not amended <= entry_ids:
            raise ValueError(f"check {check_id} amends an entry outside the check")
        if len(amended) != len(loaded_entries) - 1:
            raise ValueError(f"check {check_id} amendments must form a single chain")
        unamended = [entry["id"] for entry in loaded_entries if entry["id"] not in amended]
        if len(unamended) != 1:
            raise ValueError(f"check {check_id} must have exactly one authoritative entry")
        for other in checks.values():
            if other is row or other["trap"] != trap_id:
                continue
            if start < other["end_day"] and other["start_day"] < end:
                raise ValueError(f"checks for {trap_id} overlap: {check_id}")
        row.update({"trap": trap_id, "start_day": start, "end_day": end,
                    "entry_ids": [entry["id"] for entry in loaded_entries],
                    "authoritative_entry_id": unamended[0]})
    return checks, entries


def _load_entry(row: Row, check_id: str, entries: dict[str, Row]) -> Row:
    entry_id = _required_text(row, "id")
    if entry_id in entries:
        raise ValueError(f"duplicate entry id: {entry_id}")
    count = _required_integer(row, "count")
    rank = _optional_text(row, "rank")
    genus = _optional_text(row, "genus")
    species = _optional_text(row, "species")
    if count == 0:
        # 零计数可只记录无鉴定的零捕获；有种级鉴定的零计数构成该物种的未出现证据
        if rank is None:
            if genus is not None or species is not None:
                raise ValueError(f"entry {entry_id}: rank is required when genus or species is given")
        elif rank not in (GENUS, SPECIES):
            raise ValueError(f"entry {entry_id}: rank must be genus or species")
        if rank is not None and not genus:
            raise ValueError(f"entry {entry_id}: genus is required for identified entries")
        if rank == GENUS and species is not None:
            raise ValueError(f"entry {entry_id}: genus-rank entry must not name a species")
        if rank == SPECIES and not species:
            raise ValueError(f"entry {entry_id}: species is required for species-rank entry")
    else:
        if rank not in (GENUS, SPECIES):
            raise ValueError(f"entry {entry_id}: rank must be genus or species")
        if not genus:
            raise ValueError(f"entry {entry_id}: genus is required for identified catch")
        if rank == GENUS and species is not None:
            raise ValueError(f"entry {entry_id}: genus-rank entry must not name a species")
        if rank == SPECIES and not species:
            raise ValueError(f"entry {entry_id}: species is required for species-rank entry")
    entry = {"id": entry_id, "check": check_id, "count": count, "rank": rank,
             "genus": genus, "species": species,
             "source": _required_text(row, "source"),
             "amends": _optional_text(row, "amends")}
    entries[entry_id] = entry
    return entry


# ---------------------------------------------------------------- 区间与观察期

def _covering_parts(trap: Row, start: float, end: float) -> list[Row]:
    parts = []
    for placement in trap["placements"]:
        seg_start, seg_end = max(start, placement["start"]), min(end, placement["end"])
        if seg_end > seg_start:
            parts.append({"zone": placement["zone"], "start": seg_start,
                          "end": seg_end, "days": seg_end - seg_start})
    if _round6(sum(part["days"] for part in parts)) != _round6(end - start):
        raise ValueError("check window must stay within continuous trap deployment")
    return parts


def _unobserved_segments(trap: Row, checks: dict[str, Row]) -> list[tuple[str, float, float]]:
    segments = []
    trap_checks = sorted(
        (check for check in checks.values() if check["trap"] == trap["id"]),
        key=lambda check: check["start_day"],
    )
    for placement in trap["placements"]:
        cursor = placement["start"]
        for check in trap_checks:
            seg_start = max(cursor, check["start_day"])
            seg_end = min(placement["end"], check["end_day"])
            if seg_end <= seg_start:
                continue
            if seg_start > cursor:
                segments.append((placement["zone"], cursor, seg_start))
            cursor = seg_end
        if cursor < placement["end"]:
            segments.append((placement["zone"], cursor, placement["end"]))
    return segments


# ---------------------------------------------------------------- 事实构造

def _new_stats() -> Row:
    return {"traps": set(), "checks": set(), "zero_checks": set(), "batches": set(),
            "deployed_days": 0.0, "observed_days": 0.0,
            "definite": 0, "min": 0, "max": 0, "unobserved": [],
            "genera": {}, "species": {}}


def _taxon_stats(stats: Row, genus: str, species: str | None) -> Row:
    table = stats["species"] if species is not None else stats["genera"]
    key = (genus, species) if species is not None else genus
    if key not in table:
        table[key] = {"definite": 0, "min": 0, "max": 0, "checks": set(),
                      "genus_rank_checks": set(),
                      "presence": set(), "absence": set(),
                      "ambiguous_checks": set()}
    return table[key]


def _add_count(stats: Row, genus: str | None, species: str | None,
               count: int, low: int, high: int) -> None:
    stats["definite"] += count
    stats["min"] += low
    stats["max"] += high
    if genus is None:
        return
    # 属级口径同时收纳属下种级记录与未分开清点的交接批次
    genus_target = _taxon_stats(stats, genus, None)
    genus_target["definite"] += count
    genus_target["min"] += low
    genus_target["max"] += high
    if species is not None:
        target = _taxon_stats(stats, genus, species)
        target["definite"] += count
        target["min"] += low
        target["max"] += high


def _deployment_fact(trap_id: str, trap: Row, checks: dict[str, Row]) -> Row:
    return {
        "fact_id": f"trap-deployment:{trap_id}",
        "trap": trap_id,
        "deployed_days": _round6(sum(placement["days"] for placement in trap["placements"])),
        "placements": [
            {"zone": placement["zone"], "start_day": _round6(placement["start"]),
             "end_day": _round6(placement["end"]), "days": _round6(placement["days"])}
            for placement in trap["placements"]
        ],
        "checks": sorted(
            check["id"] for check in checks.values() if check["trap"] == trap_id
        ),
    }


def _check_fact(check: Row, trap: Row, stats: dict[str, Row],
                entries: dict[str, Row]) -> tuple[Row, Row | None]:
    parts = _covering_parts(trap, check["start_day"], check["end_day"])
    authoritative = entries[check["authoritative_entry_id"]]
    count = authoritative["count"]
    genus, species, rank = authoritative["genus"], authoritative["species"], authoritative["rank"]
    ambiguous = len(parts) == 2 and count > 0

    allocations = []
    batch = None
    if ambiguous:
        for part in parts:
            zone = part["zone"]
            allocations.append({"zone": zone, "observed_days": _round6(part["days"]),
                                "min_count": 0, "max_count": count})
            zone_stats = stats[zone]
            zone_stats["observed_days"] += part["days"]
            zone_stats["checks"].add(check["id"])
            zone_stats["batches"].add(check["id"])
            _add_count(zone_stats, genus, species, 0, 0, count)
            _register_check(zone_stats, check["id"], genus, species, rank, count,
                            ambiguous=True)
        batch = {
            "fact_id": f"handover-batch:{check['id']}",
            "batch": check["id"], "trap": check["trap"],
            "start_day": _round6(check["start_day"]), "end_day": _round6(check["end_day"]),
            "count": count, "genus": genus, "species": species, "rank": rank,
            "zones": [part["zone"] for part in parts],
            "allocation_rule": "the single batch may be assigned to exactly one zone",
        }
    else:
        for part in parts:
            zone = part["zone"]
            allocations.append({"zone": zone, "observed_days": _round6(part["days"]),
                                "count": count})
            zone_stats = stats[zone]
            zone_stats["observed_days"] += part["days"]
            zone_stats["checks"].add(check["id"])
            _add_count(zone_stats, genus, species, count, count, count)
            _register_check(zone_stats, check["id"], genus, species, rank, count,
                            ambiguous=False)
            if count == 0:
                zone_stats["zero_checks"].add(check["id"])

    fact = {
        "fact_id": f"trap-check:{check['id']}",
        "check": check["id"], "trap": check["trap"],
        "start_day": _round6(check["start_day"]), "end_day": _round6(check["end_day"]),
        "observed_days": _round6(check["end_day"] - check["start_day"]),
        "zero_catch": count == 0,
        "ambiguous_handover": ambiguous,
        "authoritative_entry_id": check["authoritative_entry_id"],
        "entries": [_entry_fact(entries[entry_id]) for entry_id in check["entry_ids"]],
        "allocations": allocations,
    }
    return fact, batch


def _entry_fact(entry: Row) -> Row:
    return {"entry": entry["id"], "count": entry["count"], "rank": entry["rank"],
            "genus": entry["genus"], "species": entry["species"],
            "source": entry["source"], "amends": entry["amends"]}


def _register_check(stats: Row, check_id: str, genus: str | None,
                    species: str | None, rank: str | None, count: int,
                    *, ambiguous: bool) -> None:
    if genus is None:
        return
    genus_target = _taxon_stats(stats, genus, None)
    genus_target["checks"].add(check_id)
    if rank == GENUS:
        genus_target["genus_rank_checks"].add(check_id)
    if ambiguous:
        # 批次归属未定，对属和种都不能确认是否出现
        genus_target["ambiguous_checks"].add(check_id)
        if species is not None:
            _taxon_stats(stats, genus, species)["ambiguous_checks"].add(check_id)
        return
    if rank == SPECIES:
        target = _taxon_stats(stats, genus, species)
        target["checks"].add(check_id)
        # 种级检查：正计数证明出现，零计数证明该区未出现
        target["presence" if count > 0 else "absence"].add(check_id)


def _species_universe(checks: dict[str, Row], entries: dict[str, Row]) -> list[tuple[str, str]]:
    universe = set()
    for check in checks.values():
        entry = entries[check["authoritative_entry_id"]]
        if entry["rank"] == SPECIES:
            universe.add((entry["genus"], entry["species"]))
    return sorted(universe)


def _rate(min_count: int, max_count: int, days: float) -> Row:
    if days == 0:
        return {"min_per_trap_day": None, "max_per_trap_day": None}
    return {"min_per_trap_day": _round6(min_count / days),
            "max_per_trap_day": _round6(max_count / days)}


def _zone_activity_facts(zones: dict[str, Row], stats: dict[str, Row]) -> list[Row]:
    facts = []
    for zone_id in sorted(zones):
        item = stats[zone_id]
        facts.append({
            "fact_id": f"zone-activity:{zone_id}",
            "zone": zone_id, "zone_name": zones[zone_id]["name"],
            "traps": sorted(item["traps"]),
            "checks": sorted(item["checks"]),
            "zero_catch_checks": sorted(item["zero_checks"]),
            "ambiguous_batches": sorted(item["batches"]),
            "deployed_trap_days": _round6(item["deployed_days"]),
            "effective_trap_days": _round6(item["observed_days"]),
            "unobserved_segments": item["unobserved"],
            "count_definite": item["definite"],
            "count_min": item["min"], "count_max": item["max"],
            **_rate(item["min"], item["max"], item["observed_days"]),
        })
    return facts


def _genus_facts(zones: dict[str, Row], stats: dict[str, Row]) -> list[Row]:
    genera = sorted({genus for item in stats.values() for genus in item["genera"]})
    facts = []
    for genus in genera:
        for zone_id in sorted(zones):
            item = stats[zone_id]["genera"].get(genus)
            facts.append({
                "fact_id": f"genus-comparison:{genus}:{zone_id}",
                "zone": zone_id, "genus": genus,
                "checks": sorted(item["checks"]) if item else [],
                "genus_rank_checks": sorted(item["genus_rank_checks"]) if item else [],
                "ambiguous_handover_checks": sorted(item["ambiguous_checks"]) if item else [],
                "diagnosis_complete": item is None or not item["ambiguous_checks"],
                "effective_trap_days": _round6(stats[zone_id]["observed_days"]),
                "count_definite": item["definite"] if item else 0,
                "count_min": item["min"] if item else 0,
                "count_max": item["max"] if item else 0,
                **_rate(item["min"] if item else 0, item["max"] if item else 0,
                        stats[zone_id]["observed_days"]),
            })
    return facts


def _species_facts(zones: dict[str, Row], stats: dict[str, Row],
                   universe: list[tuple[str, str]]) -> list[Row]:
    facts = []
    for genus, species in universe:
        key = (genus, species)
        for zone_id in sorted(zones):
            item = stats[zone_id]["species"].get(key)
            genus_item = stats[zone_id]["genera"].get(genus)
            nondiagnostic = set(genus_item["genus_rank_checks"]) if genus_item else set()
            ambiguous = set(item["ambiguous_checks"]) if item else set()
            absence = set(item["absence"]) if item else set()
            facts.append({
                "fact_id": f"species-comparison:{genus}-{species}:{zone_id}",
                "zone": zone_id, "genus": genus, "species": species,
                "presence_checks": sorted(item["presence"]) if item else [],
                "absence_checks": sorted(absence),
                "genus_rank_checks": sorted(nondiagnostic),
                "ambiguous_handover_checks": sorted(ambiguous),
                "diagnosis_complete": not nondiagnostic and not ambiguous,
                "effective_trap_days": _round6(stats[zone_id]["observed_days"]),
                "count_definite": item["definite"] if item else 0,
                "count_min": item["min"] if item else 0,
                "count_max": item["max"] if item else 0,
                **_rate(item["min"] if item else 0, item["max"] if item else 0,
                        stats[zone_id]["observed_days"]),
            })
    return facts


def _zone_comparison_facts(zones: dict[str, Row], stats: dict[str, Row]) -> list[Row]:
    facts = []
    zone_ids = sorted(zones)
    for left, right in zip(zone_ids, zone_ids[1:]):
        left_stats, right_stats = stats[left], stats[right]
        if left_stats["observed_days"] == 0 or right_stats["observed_days"] == 0:
            verdict = "incomparable: a zone has no effective observation"
        elif left_stats["min"] / left_stats["observed_days"] > right_stats["max"] / right_stats["observed_days"]:
            verdict = f"{left} is more active than {right}"
        elif right_stats["min"] / right_stats["observed_days"] > left_stats["max"] / left_stats["observed_days"]:
            verdict = f"{right} is more active than {left}"
        else:
            verdict = "inconclusive: possible per-trap-day ranges overlap"
        facts.append({"fact_id": f"zone-comparison:{left}:{right}",
                      "zones": [left, right], "verdict": verdict})
    return facts
