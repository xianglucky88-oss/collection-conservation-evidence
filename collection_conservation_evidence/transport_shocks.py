"""按已去重力分量的同一采样时刻三轴加速度和给定触发线记录冲击样本。"""

from math import hypot

from .contract import Row, apply_rows, number, text


def build_facts(data: Row) -> list[Row]:
    trigger = number(data, "trigger_g", minimum=0)

    def measure(row: Row) -> Row:
        magnitude = hypot(*(number(row, f"{axis}_g") for axis in ("x", "y", "z")))
        return {"crate": text(row, "crate"), "sample_time": text(row, "sample_time"),
                "resultant_g": round(magnitude, 6), "at_or_above_trigger": magnitude >= trigger}

    return apply_rows(data, measure)
