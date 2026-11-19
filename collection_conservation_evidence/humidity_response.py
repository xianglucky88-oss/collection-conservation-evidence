"""记录闭柜后单探头首次进入目标相对湿度范围的读数。"""

from .contract import Row, apply_rows, index_rows, number, read_rows, text


def build_facts(data: Row) -> list[Row]:
    lower = number(data, "lower_rh_pct", minimum=0)
    upper = number(data, "upper_rh_pct", minimum=lower)
    if upper > 100:
        raise ValueError("upper_rh_pct must not exceed 100")

    def measure(row: Row) -> Row:
        observations = index_rows(read_rows(row, "observations"), "id")
        inside = []
        for key, observation in observations.items():
            rh = number(observation, "rh_pct", minimum=0)
            minutes = number(observation, "minutes_after_close", minimum=0)
            if rh > 100:
                raise ValueError("rh_pct must not exceed 100")
            if lower <= rh <= upper:
                inside.append((minutes, key))
        minutes, key = min(inside) if inside else (None, None)
        return {"case": text(row, "case"), "first_in_band_minutes": minutes, "observation": key}

    return apply_rows(data, measure)
