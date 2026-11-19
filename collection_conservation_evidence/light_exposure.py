"""计算每段恒定照度观测的光剂量。"""

from .contract import Row, apply_rows, number, text


def build_facts(data: Row) -> list[Row]:
    def measure(row: Row) -> Row:
        duration = number(row, "hours", minimum=0)
        lux = number(row, "lux", minimum=0)
        return {"object": text(row, "object"), "case": text(row, "case"),
                "hours": duration, "lux_hours": round(lux * duration, 6)}

    return apply_rows(data, measure)
