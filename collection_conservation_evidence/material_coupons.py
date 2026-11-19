"""按单个金属试片保留外观腐蚀等级。"""

from .contract import Row, apply_rows, integer, text


def build_facts(data: Row) -> list[Row]:
    limit = integer(data, "max_grade")

    def inspect(row: Row) -> Row:
        grade = integer(row, "corrosion_grade")
        return {"container": text(row, "container"), "metal": text(row, "metal"),
                "grade": grade, "within_visual_limit": grade <= limit}

    return apply_rows(data, inspect)
