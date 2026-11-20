"""按试验轮次和柜内位置复核闭柜后的恢复时间。

基线只取单探头首次进入目标带的读数，容易把一次短暂掠过当成恢复。这里按
试验单给出的必检位置、允许记录间隔与连续合格时长，逐轮、逐位置复核：

* ``recovery_minutes``：该位置第一次进入一段**连续合格**记录、且这段记录
  至少维持要求时长的起点。它是本次闭柜中可以确认的恢复时间。
* ``recovery_confirmed_by_minutes``：维持时长首次在记录上被证实的时刻。
* ``recovery_lower_bound_minutes``：最后一次实际观察到仍在目标带外的时间。
  那一刻该位置尚未恢复，因此恢复一定晚于它——这是目前可证明的下限。
* ``unconfirmed_in_band_since_minutes``：最后一次观察仍在目标带内、但维持
  时长尚未满足时，这段带内记录的起点。它只是**候选**恢复时间：仅当该位置
  此后一直留在带内并维持够时长才成立；中途离开、记录中断或再次开柜都会让
  真正的恢复更晚。它不能当作已确认的恢复，也不是可证明的界限。

测点失联（记录间隔超过允许值）不会给出可证明的上限：位置可能在失联期间
恢复，也可能一直没恢复，因此未确认位置只给下限与候选时间。

连续合格要求相邻记录都在目标相对湿度范围内，且相邻记录间隔不超过允许的
记录间隔。间隔过大视为测点失联，失联前后的读数不能拼成同一段；维持时长
必须在记录上真正被观察到，不能靠两端读数推断。``recovery_minutes`` 取的是
维持段的第一条记录，真实进入目标带的时刻可能比它早最多一个允许间隔。

一次开柜（``reopened_at_minutes``）之后的读数会被计数并从本次闭柜判断中
排除，只保留开柜前的结论：开柜前已完成的轮次记 ``completed_before_reopening``，
未完成的记 ``incomplete_at_reopening``。

其他证据单列：``transient_in_band``（维持不足就离开目标带的带内段）、
``severed_in_band_runs``（被过大的记录间隔切断的带内段）、``observation_gaps``
与 ``monitoring_ended_early``（该位置在轮次结束前就没有记录了）。这些证据只
列到首个确认恢复为止，恢复之后的正常波动不再计入。
"""

from __future__ import annotations

from .contract import Row, index_rows, number, read_rows, text


def _text_list(data: Row, field: str) -> list[str]:
    values = data[field]
    if not isinstance(values, list) or not values:
        raise ValueError(f"{field} must be a nonempty list")
    result: list[str] = []
    for value in values:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field} must contain nonempty text")
        if value in result:
            raise ValueError(f"duplicate {field}: {value}")
        result.append(value)
    return result


def _read_observations(row: Row) -> list[Row]:
    observations = index_rows(read_rows(row, "observations"), "id")
    result: list[Row] = []
    seen: set[tuple[str, float]] = set()
    for key, observation in observations.items():
        position = text(observation, "position")
        rh = number(observation, "rh_pct", minimum=0)
        if rh > 100:
            raise ValueError("rh_pct must not exceed 100")
        minutes = number(observation, "minutes_after_close", minimum=0)
        marker = (position, minutes)
        if marker in seen:
            raise ValueError(f"duplicate observation time for {position}: {minutes}")
        seen.add(marker)
        result.append({"id": key, "position": position, "rh_pct": rh, "minutes": minutes})
    return result


def _new_run(minutes: float) -> Row:
    return {
        "entered_minutes": minutes,
        "last_in_band_minutes": minutes,
        "minutes": [minutes],
        "ended_by": None,
        "next_observation_minutes": None,
    }


def _run_length(run: Row) -> float:
    return run["last_in_band_minutes"] - run["entered_minutes"]


def _runs(observations: list[Row], lower: float, upper: float, max_gap: float) -> list[Row]:
    """把单个位置的有序记录切成带内连续段。

    离开目标带或出现超过允许间隔的记录都会切断一段；切断前后的读数不会
    被拼成同一段。
    """
    runs: list[Row] = []
    current: Row | None = None
    for observation in observations:
        in_band = lower <= observation["rh_pct"] <= upper
        if not in_band:
            if current is not None:
                current["ended_by"] = "left_band"
                current["next_observation_minutes"] = observation["minutes"]
                runs.append(current)
                current = None
            continue
        if current is None:
            current = _new_run(observation["minutes"])
        elif observation["minutes"] - current["last_in_band_minutes"] > max_gap:
            current["ended_by"] = "observation_gap"
            current["next_observation_minutes"] = observation["minutes"]
            runs.append(current)
            current = _new_run(observation["minutes"])
        else:
            current["last_in_band_minutes"] = observation["minutes"]
            current["minutes"].append(observation["minutes"])
    if current is not None:
        runs.append(current)
    return runs


def _evaluate_position(
    position: str,
    observations: list[Row],
    *,
    required: bool,
    lower: float,
    upper: float,
    hold: float,
    max_gap: float,
    round_end: float | None,
    interrupted: bool,
) -> Row:
    result: Row = {
        "position": position,
        "required": required,
        "status": "unmonitored",
        "finding": "no_observations",
        "recovery_minutes": None,
        "recovery_confirmed_by_minutes": None,
        "recovery_lower_bound_minutes": None,
        "unconfirmed_in_band_since_minutes": None,
        "first_in_band_minutes": None,
        "first_in_band_was_transient": False,
        "last_observation_minutes": None,
        "observation_count": 0,
        "largest_observation_gap_minutes": None,
        "observation_gaps": [],
        "monitoring_ended_early": False,
        "transient_in_band": [],
        "severed_in_band_runs": [],
    }
    if not observations:
        return result

    observations = sorted(observations, key=lambda item: item["minutes"])
    last = observations[-1]
    outside = [item["minutes"] for item in observations if not lower <= item["rh_pct"] <= upper]
    in_band = [item["minutes"] for item in observations if lower <= item["rh_pct"] <= upper]
    runs = _runs(observations, lower, upper, max_gap)
    last_in_band = lower <= last["rh_pct"] <= upper

    qualifying = [run for run in runs if _run_length(run) >= hold]
    confirmed_run = (
        min(qualifying, key=lambda item: item["entered_minutes"]) if qualifying else None
    )
    # 已确认恢复之后的带内波动不再作为“短暂进入”证据，避免掩盖真正的恢复点。
    cutoff = confirmed_run["entered_minutes"] if confirmed_run is not None else float("inf")
    transient_runs = [
        run
        for run in runs
        if run["entered_minutes"] < cutoff
        and run["ended_by"] == "left_band"
        and _run_length(run) < hold
    ]
    severed_runs = [
        run
        for run in runs
        if run["entered_minutes"] < cutoff and run["ended_by"] == "observation_gap"
    ]

    result.update(
        {
            "first_in_band_minutes": in_band[0] if in_band else None,
            "last_observation_minutes": last["minutes"],
            "observation_count": len(observations),
            "largest_observation_gap_minutes": max(
                (
                    current["minutes"] - previous["minutes"]
                    for previous, current in zip(observations, observations[1:])
                ),
                default=None,
            ),
            "observation_gaps": [
                {
                    "from_minutes": previous["minutes"],
                    "to_minutes": current["minutes"],
                    "gap_minutes": current["minutes"] - previous["minutes"],
                }
                for previous, current in zip(observations, observations[1:])
                if current["minutes"] - previous["minutes"] > max_gap
            ],
            "monitoring_ended_early": (
                round_end is not None and last["minutes"] < round_end - max_gap
            ),
            "transient_in_band": [
                {
                    "entered_minutes": run["entered_minutes"],
                    "last_in_band_minutes": run["last_in_band_minutes"],
                    "left_band_at_minutes": run["next_observation_minutes"],
                    "observed_in_band_minutes": _run_length(run),
                }
                for run in transient_runs
            ],
            "severed_in_band_runs": [
                {
                    "entered_minutes": run["entered_minutes"],
                    "last_in_band_minutes": run["last_in_band_minutes"],
                    "next_observation_minutes": run["next_observation_minutes"],
                    "observed_in_band_minutes": _run_length(run),
                }
                for run in severed_runs
            ],
        }
    )
    if result["first_in_band_minutes"] is not None:
        result["first_in_band_was_transient"] = any(
            item["entered_minutes"] == result["first_in_band_minutes"]
            for item in result["transient_in_band"]
        )

    if confirmed_run is not None:
        confirmed_at = next(
            minutes
            for minutes in confirmed_run["minutes"]
            if minutes - confirmed_run["entered_minutes"] >= hold
        )
        earlier_outside = [
            minute for minute in outside if minute < confirmed_run["entered_minutes"]
        ]
        result.update(
            {
                "status": "confirmed",
                "finding": "confirmed_recovery",
                "recovery_minutes": confirmed_run["entered_minutes"],
                "recovery_confirmed_by_minutes": confirmed_at,
                "recovery_lower_bound_minutes": earlier_outside[-1] if earlier_outside else None,
            }
        )
        return result

    result["status"] = "unconfirmed"
    result["recovery_lower_bound_minutes"] = outside[-1] if outside else None
    if last_in_band:
        result["unconfirmed_in_band_since_minutes"] = runs[-1]["entered_minutes"]
        if interrupted:
            result["finding"] = "interrupted_before_hold_met"
        elif result["monitoring_ended_early"]:
            result["finding"] = "monitoring_lost_during_candidate_stretch"
        else:
            result["finding"] = "hold_not_met_at_end_of_monitoring"
    elif in_band:
        result["finding"] = "outside_band_at_last_observation"
    else:
        result["finding"] = "never_in_band"
    return result


def _build_round(
    row: Row,
    *,
    required_positions: list[str],
    lower: float,
    upper: float,
    hold: float,
    max_gap: float,
) -> Row:
    reopened = row.get("reopened_at_minutes")
    if reopened is not None:
        reopened = number(row, "reopened_at_minutes", minimum=0)
        if reopened <= 0:
            raise ValueError("reopened_at_minutes must be positive")

    observations = _read_observations(row)
    excluded_ids: list[str] = []
    if reopened is not None:
        kept = [item for item in observations if item["minutes"] < reopened]
        excluded_ids = [item["id"] for item in observations if item["minutes"] >= reopened]
        observations = kept

    by_position: dict[str, list[Row]] = {}
    for observation in observations:
        by_position.setdefault(observation["position"], []).append(observation)

    if reopened is not None:
        round_end: float | None = reopened
    elif observations:
        round_end = max(item["minutes"] for item in observations)
    else:
        round_end = None

    def evaluate(position: str, required: bool) -> Row:
        return _evaluate_position(
            position,
            by_position.get(position, []),
            required=required,
            lower=lower,
            upper=upper,
            hold=hold,
            max_gap=max_gap,
            round_end=round_end,
            interrupted=reopened is not None,
        )

    required = [evaluate(position, True) for position in required_positions]
    extras = [
        evaluate(position, False)
        for position in by_position
        if position not in required_positions
    ]

    unconfirmed = [item["position"] for item in required if item["status"] != "confirmed"]
    unmonitored = [item["position"] for item in required if item["status"] == "unmonitored"]
    complete = not unconfirmed
    recovery = slowest = None
    if complete:
        recovery = max(item["recovery_minutes"] for item in required)
        slowest = next(
            item["position"] for item in required if item["recovery_minutes"] == recovery
        )

    if reopened is not None:
        conclusion = "completed_before_reopening" if complete else "incomplete_at_reopening"
    else:
        conclusion = "complete" if complete else "incomplete"

    return {
        "fact_id": text(row, "id"),
        "fact_type": "round",
        "case": text(row, "case"),
        "layout": text(row, "layout"),
        "required_positions": required_positions,
        "required_hold_minutes": hold,
        "max_observation_gap_minutes": max_gap,
        "reopened_at_minutes": reopened,
        "excluded_observation_ids": excluded_ids,
        "monitored_until_minutes": round_end,
        "round_status": "complete" if complete else "incomplete",
        "round_conclusion": conclusion,
        "recovery_minutes": recovery,
        "slowest_position": slowest,
        "confirmed_required_position_count": len(required) - len(unconfirmed),
        "unconfirmed_required_positions": unconfirmed,
        "unmonitored_required_positions": unmonitored,
        "positions": required + extras,
    }


def _build_layout(layout: str, round_facts: list[Row]) -> Row:
    complete = [fact for fact in round_facts if fact["round_status"] == "complete"]
    incomplete = [fact for fact in round_facts if fact["round_status"] != "complete"]

    required_positions: list[str] = []
    for fact in round_facts:
        for position in fact["required_positions"]:
            if position not in required_positions:
                required_positions.append(position)

    positions: list[Row] = []
    unresolved: list[Row] = []
    evidenced: list[Row] = []
    for position in required_positions:
        results = [
            (fact, next(item for item in fact["positions"] if item["position"] == position))
            for fact in round_facts
        ]
        confirmed = [(fact, item) for fact, item in results if item["status"] == "confirmed"]
        confirmed_minutes = [item["recovery_minutes"] for _, item in confirmed]
        positions.append(
            {
                "position": position,
                "confirmed_round_ids": [fact["fact_id"] for fact, _ in confirmed],
                "unconfirmed_round_ids": [
                    fact["fact_id"] for fact, item in results if item["status"] != "confirmed"
                ],
                "fastest_confirmed_minutes": min(confirmed_minutes, default=None),
                "slowest_confirmed_minutes": max(confirmed_minutes, default=None),
            }
        )
        for fact, item in confirmed:
            evidenced.append(
                {
                    "round_id": fact["fact_id"],
                    "position": position,
                    "recovery_minutes": item["recovery_minutes"],
                    "recovery_confirmed_by_minutes": item["recovery_confirmed_by_minutes"],
                }
            )
        for fact, item in results:
            if item["status"] != "confirmed":
                unresolved.append(
                    {
                        "round_id": fact["fact_id"],
                        "position": position,
                        "status": item["status"],
                        "finding": item["finding"],
                        "recovery_lower_bound_minutes": item["recovery_lower_bound_minutes"],
                        "unconfirmed_in_band_since_minutes": item[
                            "unconfirmed_in_band_since_minutes"
                        ],
                        "monitoring_ended_early": item["monitoring_ended_early"],
                    }
                )

    slowest = max((item["recovery_minutes"] for item in evidenced), default=None)
    fastest = min((item["recovery_minutes"] for item in evidenced), default=None)
    slowest_positions = [
        {
            "round_id": item["round_id"],
            "position": item["position"],
            "recovery_minutes": item["recovery_minutes"],
            "recovery_confirmed_by_minutes": item["recovery_confirmed_by_minutes"],
        }
        for item in evidenced
        if item["recovery_minutes"] == slowest
    ]
    rounds_without_any = [
        fact["fact_id"]
        for fact in round_facts
        if not any(
            item["status"] == "confirmed" for item in fact["positions"] if item["required"]
        )
    ]
    incomplete_ids = [fact["fact_id"] for fact in incomplete]
    blocked_positions = sorted({item["position"] for item in unresolved})

    # 只要还有轮次没完成，真实的最慢恢复就可能晚于目前有证据的任何一个位置，
    # 因此这个布置的恢复时间还不能用来排展。
    planning_ready = bool(round_facts) and not incomplete and slowest is not None
    planning_recovery = slowest if planning_ready else None
    planning_confirmed_by = (
        max(
            item["recovery_confirmed_by_minutes"]
            for item in evidenced
            if item["recovery_minutes"] == planning_recovery
        )
        if planning_ready
        else None
    )

    # 已确认的恢复与未确认位置的下限共同给出最慢恢复的最小可能值：无论后续
    # 补测结果如何，这个布置的恢复时间都不会早于这一时刻。
    lower_bounds = [
        item["recovery_lower_bound_minutes"]
        for item in unresolved
        if item["recovery_lower_bound_minutes"] is not None
    ]
    recovery_floor = max(
        [*lower_bounds, *([slowest] if slowest is not None else [])], default=None
    )

    cases: list[str] = []
    for fact in round_facts:
        if fact["case"] not in cases:
            cases.append(fact["case"])

    return {
        "fact_id": f"layout:{layout}",
        "fact_type": "layout",
        "layout": layout,
        "cases": cases,
        "round_ids": [fact["fact_id"] for fact in round_facts],
        "round_count": len(round_facts),
        "complete_round_ids": [fact["fact_id"] for fact in complete],
        "incomplete_round_ids": incomplete_ids,
        "reopened_round_ids": [
            fact["fact_id"] for fact in round_facts if fact["reopened_at_minutes"] is not None
        ],
        "rounds_without_any_confirmed_recovery": rounds_without_any,
        "positions": positions,
        "recovery_range_minutes": {"fastest": fastest, "slowest": slowest},
        "slowest_confirmed_recovery_minutes": slowest,
        "slowest_evidenced_positions": slowest_positions,
        "recovery_floor_minutes": recovery_floor,
        "unresolved_positions": unresolved,
        "planning_ready": planning_ready,
        "planning_recovery_minutes": planning_recovery,
        "planning_confirmed_by_minutes": planning_confirmed_by,
        "planning_blocked_by_round_ids": [] if planning_ready else incomplete_ids,
        "planning_blocked_by_positions": [] if planning_ready else blocked_positions,
    }


def build_facts(data: Row) -> list[Row]:
    lower = number(data, "lower_rh_pct", minimum=0)
    upper = number(data, "upper_rh_pct", minimum=lower)
    if upper > 100:
        raise ValueError("upper_rh_pct must not exceed 100")
    hold = number(data, "required_hold_minutes", minimum=0)
    if hold <= 0:
        raise ValueError("required_hold_minutes must be positive")
    max_gap = number(data, "max_observation_gap_minutes", minimum=0)
    if max_gap <= 0:
        raise ValueError("max_observation_gap_minutes must be positive")
    required_positions = _text_list(data, "required_positions")

    rounds: list[Row] = []
    by_layout: dict[str, list[Row]] = {}
    for row in index_rows(read_rows(data), "id").values():
        fact = _build_round(
            row,
            required_positions=required_positions,
            lower=lower,
            upper=upper,
            hold=hold,
            max_gap=max_gap,
        )
        rounds.append(fact)
        by_layout.setdefault(fact["layout"], []).append(fact)

    facts = rounds
    used_ids = {fact["fact_id"] for fact in rounds}
    for layout, layout_rounds in by_layout.items():
        fact = _build_layout(layout, layout_rounds)
        if fact["fact_id"] in used_ids:
            raise ValueError(f"fact id conflicts with layout fact id: {fact['fact_id']}")
        used_ids.add(fact["fact_id"])
        facts.append(fact)
    return facts
