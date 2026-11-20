"""单件纸质藏品的年度光剂量区间核算。

输入为分段照度读数（含重叠与更正读数）、只能确定在时间窗内的位置交接、
探头适用位置以及年度额度，输出到指定日期为止已用剂量的可信下界/上界、
已证明暗库停留与无人测量空档的区分，以及给定照度下可确认的剩余陈列时长。
"""

from __future__ import annotations

from datetime import datetime, timedelta

from .contract import Row, number, text

_DARK = "dark"
_DISPLAY = "display"


def _r6(value: float | None) -> float | None:
    if value is None:
        return None
    return round(value, 6)


def _dt(row: Row, field: str) -> datetime:
    value = text(row, field)
    try:
        return datetime.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"{field} must be an ISO 8601 timestamp: {value}") from error


def _hours(start: datetime, end: datetime) -> float:
    return (end - start).total_seconds() / 3600.0


def _subtract(ranges: list[tuple[datetime, datetime]], cut_start: datetime,
              cut_end: datetime) -> list[tuple[datetime, datetime]]:
    result = []
    for start, end in ranges:
        if cut_end <= start or cut_start >= end:
            result.append((start, end))
            continue
        if cut_start > start:
            result.append((start, min(cut_start, end)))
        if cut_end < end:
            result.append((max(cut_end, start), end))
    return result


def build_facts(data: Row) -> list[Row]:
    obj = text(data, "object")
    window_start = _dt(data, "window_start")
    as_of = _dt(data, "as_of")
    aware = window_start.tzinfo is not None
    if (as_of.tzinfo is not None) != aware:
        raise ValueError("all timestamps must use the same timezone convention")
    if not window_start < as_of:
        raise ValueError("window_start must be earlier than as_of")
    query_lux = number(data, "query_lux", minimum=0)
    if query_lux <= 0:
        raise ValueError("query_lux must be greater than zero")

    def _rows(field: str) -> list[Row]:
        value = data.get(field, [])
        if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
            raise ValueError(f"{field} must contain JSON objects")
        return value

    locations: dict[str, Row] = {}
    for row in _rows("locations"):
        loc_id = text(row, "id")
        if loc_id in locations:
            raise ValueError(f"duplicate location: {loc_id}")
        kind = text(row, "kind")
        if kind not in (_DARK, _DISPLAY):
            raise ValueError(f"location kind must be {_DARK} or {_DISPLAY}: {loc_id}")
        max_lux = row.get("max_lux")
        if kind == _DARK:
            if max_lux is not None:
                raise ValueError(f"dark location must not declare max_lux: {loc_id}")
        elif max_lux is not None:
            max_lux = number(row, "max_lux", minimum=0)
        locations[loc_id] = {"id": loc_id, "kind": kind, "max_lux": max_lux}
    if not locations:
        raise ValueError("at least one location is required")

    probes: dict[str, str] = {}
    for row in _rows("probes"):
        probe_id = text(row, "id")
        if probe_id in probes:
            raise ValueError(f"duplicate probe: {probe_id}")
        loc_id = text(row, "location")
        if loc_id not in locations:
            raise ValueError(f"probe {probe_id} references unknown location: {loc_id}")
        probes[probe_id] = loc_id

    raw_handovers = _rows("handovers")
    handovers: list[Row] = []
    previous_to: str | None = None
    for row in raw_handovers:
        hid = text(row, "id")
        if any(h["id"] == hid for h in handovers):
            raise ValueError(f"duplicate handover: {hid}")
        frm, to = text(row, "from"), text(row, "to")
        if frm not in locations or to not in locations:
            raise ValueError(f"handover {hid} references unknown location")
        if previous_to is not None and frm != previous_to:
            raise ValueError(f"handover {hid} must continue from {previous_to}")
        earliest, latest = _dt(row, "earliest"), _dt(row, "latest")
        if not earliest <= latest:
            raise ValueError(f"handover {hid}: earliest must be no later than latest")
        if not (window_start <= earliest and latest <= as_of):
            raise ValueError(f"handover {hid} must fall within the audit window")
        handovers.append({"id": hid, "from": frm, "to": to,
                          "earliest": earliest, "latest": latest})
        previous_to = to
    for earlier, later in zip(handovers, handovers[1:]):
        if later["earliest"] < earlier["latest"]:
            raise ValueError("handover windows must not overlap")
    initial_location = handovers[0]["from"] if handovers else next(iter(locations))

    reading_rows: dict[str, Row] = {}
    for row in _rows("readings"):
        rid = text(row, "id")
        if rid in reading_rows:
            raise ValueError(f"duplicate reading: {rid}")
        reading_rows[rid] = row

    parsed: list[Row] = []
    for rid, row in reading_rows.items():
        probe_id = text(row, "probe")
        if probe_id not in probes:
            raise ValueError(f"reading {rid} references unknown probe: {probe_id}")
        start, end = _dt(row, "start"), _dt(row, "end")
        if not start < end:
            raise ValueError(f"reading {rid}: start must be earlier than end")
        if (start.tzinfo is not None) != aware:
            raise ValueError("all timestamps must use the same timezone convention")
        corrects = row.get("corrects")
        if corrects is not None:
            corrects = text(row, "corrects")
            if corrects not in reading_rows:
                raise ValueError(f"reading {rid} corrects unknown reading: {corrects}")
            target = reading_rows[corrects]
            start, end = max(start, _dt(target, "start")), min(end, _dt(target, "end"))
            if start >= end:
                raise ValueError(f"reading {rid} has no overlap with the reading it corrects")
        start, end = max(start, window_start), min(end, as_of)
        if start >= end:
            raise ValueError(f"reading {rid} falls outside the audit window")
        parsed.append({"id": rid, "raw_start": start, "raw_end": end,
                       "lux": number(row, "lux", minimum=0),
                       "location": probes[probe_id],
                       "corrects": corrects})

    # 更正读数在其实际测量范围内替代被更正读数：把该范围从原读数中挖空。
    scopes: list[Row] = []
    for item in parsed:
        ranges = [(item["raw_start"], item["raw_end"])]
        for correction in parsed:
            if correction["corrects"] == item["id"]:
                ranges = _subtract(ranges, correction["raw_start"], correction["raw_end"])
        scopes.append({"id": item["id"], "ranges": ranges,
                       "lux": item["lux"], "location": item["location"],
                       "corrects": item["corrects"],
                       "superseded": not ranges})

    quota_rows = data.get("annual_quotas", {})
    if not isinstance(quota_rows, dict):
        raise ValueError("annual_quotas must be a JSON object keyed by year")
    quotas: dict[int, float] = {}
    for key, value in quota_rows.items():
        if not (isinstance(key, str) and len(key) == 4 and key.isdigit()):
            raise ValueError(f"annual quota key must be a 4-digit year: {key!r}")
        quotas[int(key)] = number({"v": value}, "v", minimum=0)
    segments = _build_segments(
        window_start, as_of, locations, handovers, initial_location, scopes,
    )
    years = _aggregate_years(segments)
    remaining, warnings = _remaining_and_warnings(segments, years, quotas, query_lux)
    dark_stays, gaps = _stays_and_gaps(segments)
    handover_view = _handover_view(handovers, segments)

    facts: list[Row] = [{
        "fact_id": "audit",
        "object": obj,
        "window_start": window_start.isoformat(),
        "as_of": as_of.isoformat(),
        "query_lux": query_lux,
        "measurement_scopes": [
            {"reading_id": s["id"], "location": s["location"],
             "ranges": [{"start": r_start.isoformat(), "end": r_end.isoformat()}
                        for r_start, r_end in s["ranges"]],
             "lux": s["lux"], "corrects": s["corrects"],
             "superseded": s["superseded"]}
            for s in sorted(scopes,
                            key=lambda s: (s["ranges"][0][0] if s["ranges"]
                                           else datetime.max.replace(tzinfo=window_start.tzinfo),
                                           s["id"]))
        ],
        "years": {str(year): summary for year, summary in years.items()},
        "remaining_display": remaining,
        "dark_stays": dark_stays,
        "unmeasured_gaps": gaps,
        "handovers": handover_view,
        "warnings": warnings,
    }]
    for year, summary in years.items():
        facts.append({"fact_id": f"year:{year}", **summary})
    for index, seg in enumerate(segments):
        facts.append({
            "fact_id": f"seg:{index:03d}",
            "start": seg["start"].isoformat(),
            "end": seg["end"].isoformat(),
            "hours": _r6(seg["hours"]),
            "year": seg["year"],
            "category": seg["category"],
            "location": seg["location"],
            "handover_id": seg["handover_id"],
            "reading_ids": seg["reading_ids"],
            "lower_lux": _r6(seg["lower_lux"]),
            "upper_lux": _r6(seg["upper_lux"]),
            "lower_lux_hours": _r6(seg["lower_dose"]),
            "upper_lux_hours": _r6(seg["upper_dose"]),
            "upper_bound_supported": seg["upper_dose"] is not None,
        })
    return facts


def _status_at(moment: datetime, handovers: list[Row], initial_location: str) -> tuple[str, str | None]:
    """返回该时刻的归属：(位置 id 或交接窗口 id, 交接窗口 id 或 None)。"""
    for handover in handovers:
        if handover["earliest"] <= moment < handover["latest"]:
            return handover["id"], handover["id"]
    current = initial_location
    for handover in handovers:
        if moment >= handover["latest"]:
            current = handover["to"]
    return current, None


def _build_segments(window_start, as_of, locations, handovers, initial_location, scopes):
    points = {window_start, as_of}
    for scope in scopes:
        for start, end in scope["ranges"]:
            points.update((start, end))
    for handover in handovers:
        points.update((handover["earliest"], handover["latest"]))
    year = window_start.year
    while year < as_of.year:
        boundary = datetime(year + 1, 1, 1, tzinfo=window_start.tzinfo)
        if window_start < boundary < as_of:
            points.add(boundary)
        year += 1
    points = sorted(p for p in points if window_start <= p <= as_of)

    segments = []
    for start, end in zip(points, points[1:]):
        if start == end:
            continue
        midpoint = start + (end - start) / 2
        location_key, handover_id = _status_at(midpoint, handovers, initial_location)
        if handover_id is not None:
            handover = next(h for h in handovers if h["id"] == handover_id)
            candidate_locs = [handover["from"], handover["to"]]
        else:
            candidate_locs = [location_key]

        covering = [
            s for s in scopes
            if s["location"] in candidate_locs
            and any(r_start <= midpoint <= r_end for r_start, r_end in s["ranges"])
        ]
        hours = _hours(start, end)
        reading_ids = sorted(s["id"] for s in covering)
        if covering:
            lower_lux = min(s["lux"] for s in covering)
            upper_lux = max(s["lux"] for s in covering)
            category = "measured"
            location = location_key
        else:
            lower_lux = 0.0
            display_maxes = [
                locations[loc]["max_lux"]
                for loc in candidate_locs if locations[loc]["kind"] == _DISPLAY
            ]
            if not display_maxes:
                # 候选位置全部是暗库：无论交接是否发生，照度都可证明为零。
                upper_lux = 0.0
                category = "dark"
                location = location_key
            else:
                known = [m for m in display_maxes if m is not None]
                upper_lux = max(known) if known else None
                category = "handover" if handover_id is not None else "gap"
                location = location_key
        lower_dose = lower_lux * hours
        upper_dose = None if upper_lux is None else upper_lux * hours
        segments.append({
            "start": start, "end": end, "hours": hours,
            "year": start.year,
            "category": category, "location": location,
            "handover_id": handover_id, "reading_ids": reading_ids,
            "lower_lux": float(lower_lux),
            "upper_lux": (None if upper_lux is None else float(upper_lux)),
            "lower_dose": lower_dose, "upper_dose": upper_dose,
        })
    return segments


def _aggregate_years(segments: list[Row]) -> dict[int, Row]:
    years: dict[int, Row] = {}
    for seg in segments:
        summary = years.setdefault(seg["year"], {
            "year": seg["year"],
            "lower_lux_hours": 0.0,
            "upper_lux_hours": 0.0,
            "upper_bound_supported": True,
            "measured_lower_lux_hours": 0.0,
            "measured_upper_lux_hours": 0.0,
            "dark_hours": 0.0,
            "unmeasured_hours": 0.0,
            "unmeasured_lower_lux_hours": 0.0,
            "unmeasured_upper_lux_hours": 0.0,
            "unmeasured_upper_supported": True,
        })
        summary["lower_lux_hours"] += seg["lower_dose"]
        if seg["upper_dose"] is None:
            summary["upper_bound_supported"] = False
            summary["unmeasured_upper_supported"] = False
        else:
            summary["upper_lux_hours"] += seg["upper_dose"]
        if seg["category"] == "measured":
            summary["measured_lower_lux_hours"] += seg["lower_dose"]
            summary["measured_upper_lux_hours"] += seg["upper_dose"]
        elif seg["category"] == "dark":
            summary["dark_hours"] += seg["hours"]
        else:
            summary["unmeasured_hours"] += seg["hours"]
            summary["unmeasured_lower_lux_hours"] += seg["lower_dose"]
            if seg["upper_dose"] is not None:
                summary["unmeasured_upper_lux_hours"] += seg["upper_dose"]
    for summary in years.values():
        for key in ("lower_lux_hours", "upper_lux_hours",
                    "measured_lower_lux_hours", "measured_upper_lux_hours",
                    "dark_hours", "unmeasured_hours",
                    "unmeasured_lower_lux_hours", "unmeasured_upper_lux_hours"):
            summary[key] = _r6(summary[key])
        if not summary["upper_bound_supported"]:
            summary["upper_lux_hours"] = None
        if not summary["unmeasured_upper_supported"]:
            summary["unmeasured_upper_lux_hours"] = None
    return dict(sorted(years.items()))


def _segment_ids(segments: list[Row], predicate) -> list[str]:
    return [f"seg:{i:03d}" for i, seg in enumerate(segments) if predicate(seg)]


def _remaining_and_warnings(segments, years: dict[int, Row],
                            quotas: dict[int, float], query_lux: float):
    remaining, warnings = [], []
    for year, summary in years.items():
        if year not in quotas:
            warnings.append({
                "code": "missing_annual_quota", "year": year,
                "responsible": _segment_ids(segments, lambda s, y=year: s["year"] == y),
            })
            continue
        quota = quotas[year]
        lower, upper = summary["lower_lux_hours"], summary["upper_lux_hours"]
        entry = {
            "year": year,
            "annual_quota_lux_hours": quota,
            "guaranteed_remaining_hours": None,
            "best_effort_remaining_hours": _r6(max(0.0, (quota - lower) / query_lux)),
        }
        same_year = lambda s, y=year: s["year"] == y
        if upper is None:
            warnings.append({
                "code": "upper_bound_unsupported", "year": year,
                "responsible": _segment_ids(
                    segments, lambda s: same_year(s) and s["upper_dose"] is None),
            })
        else:
            entry["guaranteed_remaining_hours"] = _r6(
                max(0.0, (quota - upper) / query_lux))
            if lower > quota:
                warnings.append({
                    "code": "confirmed_over_limit", "year": year,
                    "responsible": _segment_ids(
                        segments, lambda s: same_year(s) and s["category"] == "measured"),
                })
            elif upper > quota:
                warnings.append({
                    "code": "possible_exhaustion", "year": year,
                    "responsible": _segment_ids(
                        segments,
                        lambda s: same_year(s) and s["category"] in ("gap", "handover")),
                })
        remaining.append(entry)
    return remaining, warnings


def _stays_and_gaps(segments: list[Row]):
    dark_stays, gaps = [], []
    gap_index = 0

    def flush(target, entry, start_index, end_index):
        if entry is None:
            return
        entry["start"] = entry["start"].isoformat()
        entry["end"] = entry["end"].isoformat()
        entry["hours"] = _r6(entry["hours"])
        entry["lower_lux_hours"] = _r6(entry.pop("lower_dose"))
        entry["upper_lux_hours"] = _r6(entry.pop("upper_dose"))
        entry["segment_ids"] = [f"seg:{i:03d}" for i in range(start_index, end_index)]
        target.append(entry)

    dark_entry = None
    gap_entry = None
    dark_start = gap_start = 0
    for index, seg in enumerate(segments):
        if seg["category"] == "dark":
            if dark_entry is None or dark_entry["location"] != seg["location"]:
                flush(dark_stays, dark_entry, dark_start, index)
                dark_entry = {"id": f"dark-{len(dark_stays) + 1:03d}",
                              "location": seg["location"], "start": seg["start"],
                              "end": seg["end"], "hours": 0.0,
                              "lower_dose": 0.0, "upper_dose": 0.0}
                dark_start = index
            dark_entry["end"] = seg["end"]
            dark_entry["hours"] += seg["hours"]
        else:
            flush(dark_stays, dark_entry, dark_start, index)
            dark_entry = None
        if seg["category"] in ("gap", "handover"):
            same = (gap_entry is not None and gap_entry["location"] == seg["location"]
                    and gap_entry.get("handover_id") == seg["handover_id"])
            if not same:
                flush(gaps, gap_entry, gap_start, index)
                gap_index += 1
                gap_entry = {"id": f"gap-{gap_index:03d}",
                             "location": seg["location"],
                             "handover_id": seg["handover_id"],
                             "cause": ("handover_window" if seg["handover_id"]
                                       else "no_measurement"),
                             "start": seg["start"], "end": seg["end"],
                             "hours": 0.0, "lower_dose": 0.0,
                             "upper_dose": 0.0,
                             "upper_bound_supported": True}
                gap_start = index
            gap_entry["end"] = seg["end"]
            gap_entry["hours"] += seg["hours"]
            gap_entry["lower_dose"] += seg["lower_dose"]
            if seg["upper_dose"] is None:
                gap_entry["upper_dose"] = None
                gap_entry["upper_bound_supported"] = False
            elif gap_entry["upper_dose"] is not None:
                gap_entry["upper_dose"] += seg["upper_dose"]
        else:
            flush(gaps, gap_entry, gap_start, index)
            gap_entry = None
    flush(dark_stays, dark_entry, dark_start, len(segments))
    flush(gaps, gap_entry, gap_start, len(segments))
    return dark_stays, gaps


def _handover_view(handovers: list[Row], segments: list[Row]) -> list[Row]:
    view = []
    for handover in handovers:
        parts = [s for s in segments if s["handover_id"] == handover["id"]]
        measured = [s for s in parts if s["category"] == "measured"]
        unmeasured = [s for s in parts if s["category"] != "measured"]

        def sum_dose(rows, field):
            if any(s[field] is None for s in rows):
                return None
            return sum(s[field] for s in rows)

        view.append({
            "id": handover["id"], "from": handover["from"], "to": handover["to"],
            "earliest": handover["earliest"].isoformat(),
            "latest": handover["latest"].isoformat(),
            "hours": _r6(sum(s["hours"] for s in parts)),
            "measured_hours": _r6(sum(s["hours"] for s in measured)),
            "measured_lower_lux_hours": _r6(sum_dose(measured, "lower_dose")),
            "measured_upper_lux_hours": _r6(sum_dose(measured, "upper_dose")),
            "unmeasured_hours": _r6(sum(s["hours"] for s in unmeasured)),
            "unmeasured_lower_lux_hours": _r6(sum_dose(unmeasured, "lower_dose")),
            "unmeasured_upper_lux_hours": _r6(sum_dose(unmeasured, "upper_dose")),
            "lower_lux_hours": _r6(sum_dose(parts, "lower_dose")),
            "upper_lux_hours": _r6(sum_dose(parts, "upper_dose")),
            "upper_bound_supported": sum_dose(parts, "upper_dose") is not None,
        })
    return view
