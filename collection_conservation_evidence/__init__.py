"""馆藏预防性保护"""

from . import light_exposure, trap_activity, transport_shocks, material_coupons, humidity_response
from .contract import Row

PIPELINES = {
    "light-exposure": light_exposure.build_facts,
    "trap-activity": trap_activity.build_facts,
    "transport-shocks": transport_shocks.build_facts,
    "material-coupons": material_coupons.build_facts,
    "humidity-response": humidity_response.build_facts,
}


def process(pipeline: str, data: Row) -> list[Row]:
    """Validate and transform an independent input dataset into keyed facts."""
    if pipeline not in PIPELINES:
        raise ValueError(f"unknown pipeline: {pipeline}")
    if not isinstance(data, dict):
        raise ValueError("input must be a JSON object")
    return PIPELINES[pipeline](data)
