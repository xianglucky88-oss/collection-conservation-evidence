"""按连续布放已知的单次检查计算每诱捕盒日虫数。"""

from .contract import Row, apply_rows, integer, number, text


def build_facts(data: Row) -> list[Row]:
    def measure(row: Row) -> Row:
        days = number(row, "deployed_days", minimum=0)
        if days == 0:
            raise ValueError("deployed_days must be positive")
        count = integer(row, "count")
        return {"trap": text(row, "trap"), "taxon": text(row, "taxon"), "count": count,
                "per_trap_day": round(count / days, 6)}

    return apply_rows(data, measure)
