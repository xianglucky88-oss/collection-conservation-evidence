"""馆藏预防性保护"""

from . import (
    humidity_response,
    light_exposure,
    material_coupons,
    material_effects,
    transport_shocks,
    trap_activity,
)
from .contract import Row

PIPELINES = {
    "light-exposure": light_exposure.build_facts,
    "trap-activity": trap_activity.build_facts,
    "transport-shocks": transport_shocks.build_facts,
    "material-coupons": material_coupons.build_facts,
    "material-effects": material_effects.build_facts,
    "humidity-response": humidity_response.build_facts,
}


def process(pipeline: str, data: Row) -> list[Row]:
    """Validate and transform an independent input dataset into keyed facts."""
    if pipeline not in PIPELINES:
        raise ValueError(f"unknown pipeline: {pipeline}")
    if not isinstance(data, dict):
        raise ValueError("input must be a JSON object")
    return PIPELINES[pipeline](data)
