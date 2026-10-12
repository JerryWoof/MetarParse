"""Fast, tolerant METAR & TAF parser.

>>> from metar_parse import parse
>>> parse("METAR LKPR 121630Z 24012G22KT 9999 FEW020 14/09 Q1012").wind.gust_kt
22.0
"""

from __future__ import annotations

from typing import Union

from .metar import parse_metar
from .models import (
    CloudLayer,
    DayTime,
    Metar,
    ParseIssue,
    Pressure,
    Remarks,
    RunwayVisualRange,
    Taf,
    TafChangeGroup,
    TrendGroup,
    Visibility,
    WeatherPhenomenon,
    Wind,
)
from .taf import parse_taf

__version__ = "0.1.0"


def parse(raw: str) -> Union[Metar, Taf]:
    """Decode a METAR, SPECI or TAF, choosing the parser from the report text."""
    words = raw.split()[:2]
    if words and (words[0] == "TAF" or words[0] in ("AMD", "COR") and words[1:] == ["TAF"]):
        return parse_taf(raw)
    return parse_metar(raw)


__all__ = [
    "CloudLayer",
    "DayTime",
    "Metar",
    "ParseIssue",
    "Pressure",
    "Remarks",
    "RunwayVisualRange",
    "Taf",
    "TafChangeGroup",
    "TrendGroup",
    "Visibility",
    "WeatherPhenomenon",
    "Wind",
    "__version__",
    "parse",
    "parse_metar",
    "parse_taf",
]
