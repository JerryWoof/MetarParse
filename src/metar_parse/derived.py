"""Derived values computed from a decoded report: flight category, relative humidity."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from .models import Metar

# Magnus formula coefficients (Alduchov & Eskridge 1996), valid roughly -40..50 °C.
_MAGNUS_A = 17.625
_MAGNUS_B = 243.04

# FAA categories, worst first.
CATEGORIES = ("LIFR", "IFR", "MVFR", "VFR")

# Visibility used for CAVOK and for the metric "9999" (10 km or more).
_UNLIMITED_VIS_SM = 10.0
# Tiny offset so that "M1SM" (less than 1 SM) falls below the 1 SM boundary.
_LESS_THAN_EPSILON = 1e-3


def relative_humidity(
    temperature_c: Optional[float], dewpoint_c: Optional[float]
) -> Optional[float]:
    """Relative humidity in percent from temperature and dew point (Magnus formula).

    Returns ``None`` if either value is missing. The result is capped at 100 %
    and rounded to one decimal.
    """
    if temperature_c is None or dewpoint_c is None:
        return None
    gamma_dew = _MAGNUS_A * dewpoint_c / (_MAGNUS_B + dewpoint_c)
    gamma_temp = _MAGNUS_A * temperature_c / (_MAGNUS_B + temperature_c)
    return round(min(100.0, 100.0 * math.exp(gamma_dew - gamma_temp)), 1)


def _visibility_sm(metar: Metar) -> Optional[float]:
    if metar.cavok:
        return _UNLIMITED_VIS_SM
    vis = metar.visibility
    if vis is None or vis.value is None:
        return None
    if vis.unit == "M" and vis.value >= 9999:
        return _UNLIMITED_VIS_SM
    miles = vis.statute_miles
    if miles is None:
        return None
    if vis.modifier == "M":
        miles = max(0.0, miles - _LESS_THAN_EPSILON)
    return miles


def _category_from_ceiling(ceiling_ft: int) -> str:
    if ceiling_ft < 500:
        return "LIFR"
    if ceiling_ft < 1000:
        return "IFR"
    if ceiling_ft <= 3000:
        return "MVFR"
    return "VFR"


def _category_from_visibility(vis_sm: float) -> str:
    if vis_sm < 1:
        return "LIFR"
    if vis_sm < 3:
        return "IFR"
    if vis_sm <= 5:
        return "MVFR"
    return "VFR"


def flight_category(metar: Metar) -> Optional[str]:
    """FAA flight category (``VFR``, ``MVFR``, ``IFR``, ``LIFR``) of a METAR.

    The ceiling is the lowest BKN/OVC/VV layer; the category is the worse of
    the ceiling and visibility categories. ``CAVOK`` counts as 10 SM with no
    ceiling, and the metric ``9999`` as more than 5 SM. Returns ``None`` if
    neither visibility nor sky condition is known.
    """
    vis_sm = _visibility_sm(metar)
    sky_known = metar.cavok or any(c.cover != "///" for c in metar.clouds)
    if vis_sm is None and not sky_known:
        return None

    candidates = []
    if vis_sm is not None:
        candidates.append(_category_from_visibility(vis_sm))
    ceiling = None if metar.cavok else metar.ceiling_ft
    candidates.append("VFR" if ceiling is None else _category_from_ceiling(ceiling))
    return min(candidates, key=CATEGORIES.index)
