"""Decoders for individual METAR/TAF groups.

Each ``parse_*`` function takes a single token (or a token list and index for
groups that can span tokens) and returns a model object, or ``None`` when the
token is not that kind of group. They never raise on bad input, so the report
parsers can simply try each decoder in turn.
"""

from __future__ import annotations

import re
from fractions import Fraction
from typing import List, Optional, Tuple

from .models import (
    CloudLayer,
    DayTime,
    Pressure,
    RunwayVisualRange,
    Visibility,
    WeatherPhenomenon,
    Wind,
)

INTENSITIES = ("-", "+", "VC")
DESCRIPTORS = ("MI", "BC", "PR", "DR", "BL", "SH", "TS", "FZ")
PHENOMENA = (
    # precipitation
    "DZ", "RA", "SN", "SG", "IC", "PL", "GR", "GS", "UP",
    # obscuration
    "BR", "FG", "FU", "VA", "DU", "SA", "HZ", "PY",
    # other
    "PO", "SQ", "FC", "SS", "DS",
)  # fmt: skip
CLOUD_COVERS = ("FEW", "SCT", "BKN", "OVC", "VV")
CLEAR_SKY = ("SKC", "CLR", "NSC", "NCD")
COMPASS = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")

_DAYTIME_RE = re.compile(r"^(\d{2})(\d{2})(\d{2})Z$")
_WIND_RE = re.compile(
    r"^(?P<dir>[0-3]\d0|VRB|///)(?P<spd>P?\d{2,3}|//)(?:G(?P<gust>P?\d{2,3}))?(?P<unit>KT|MPS|KMH)$"
)
_WIND_VAR_RE = re.compile(r"^(\d{3})V(\d{3})$")
_VIS_METRIC_RE = re.compile(r"^(\d{4})(NDV|N|NE|E|SE|S|SW|W|NW)?$")
_VIS_SM_RE = re.compile(r"^(?P<mod>[PM])?(?P<val>\d+(?:/\d+)?)SM$")
_RVR_RE = re.compile(
    r"^R(?P<rwy>\d{2}[LCR]?)/(?P<mod>[PM])?(?P<val>\d{4})"
    r"(?:V(?P<hmod>[PM])?(?P<high>\d{4}))?(?P<ft>FT)?/?(?P<tend>[UDN])?$"
)
_CLOUD_RE = re.compile(
    r"^(?P<cover>FEW|SCT|BKN|OVC|VV|///)(?P<hgt>\d{3}|///)(?P<type>CB|TCU|///)?$"
)
_TEMP_RE = re.compile(r"^(?P<t>M?\d{2}|//)/(?P<d>M?\d{2}|//)?$")
_PRESSURE_RE = re.compile(r"^(?P<unit>[QA])(?P<val>\d{4})$")


def parse_day_time(token: str) -> Optional[DayTime]:
    """``121630Z`` -> DayTime(12, 16, 30)."""
    m = _DAYTIME_RE.match(token)
    if not m:
        return None
    day, hour, minute = (int(g) for g in m.groups())
    if not (1 <= day <= 31 and hour <= 24 and minute <= 59):
        return None
    return DayTime(day, hour, minute)


def parse_period(token: str) -> Optional[Tuple[DayTime, DayTime]]:
    """TAF validity ``1218/1324`` -> (DayTime(12, 18), DayTime(13, 24))."""
    m = re.match(r"^(\d{2})(\d{2})/(\d{2})(\d{2})$", token)
    if not m:
        return None
    d1, h1, d2, h2 = (int(g) for g in m.groups())
    if not (1 <= d1 <= 31 and 1 <= d2 <= 31 and h1 <= 24 and h2 <= 24):
        return None
    return DayTime(d1, h1), DayTime(d2, h2)


def parse_hhmm(token: str) -> Optional[DayTime]:
    """Bare ``1730`` (as in ``FM1730``/``TL1800``) -> DayTime(None, 17, 30)."""
    if not re.match(r"^\d{4}$", token):
        return None
    hour, minute = int(token[:2]), int(token[2:])
    if hour > 24 or minute > 59:
        return None
    return DayTime(None, hour, minute)


def _speed(raw: Optional[str]) -> Optional[int]:
    if raw is None or raw.startswith("/"):
        return None
    return int(raw.lstrip("P"))


def parse_wind(token: str) -> Optional[Wind]:
    """``24012G22KT``, ``VRB03KT``, ``00000KT``, ``27015MPS``, ``/////KT``."""
    m = _WIND_RE.match(token)
    if not m:
        return None
    direction = m.group("dir")
    variable = direction == "VRB"
    dir_deg = None if variable or direction == "///" else int(direction)
    if dir_deg is not None and dir_deg > 360:
        return None
    return Wind(
        direction_deg=dir_deg,
        speed=_speed(m.group("spd")),
        gust=_speed(m.group("gust")),
        unit=m.group("unit"),
        variable=variable,
    )


def parse_wind_variation(token: str) -> Optional[Tuple[int, int]]:
    """``200V270`` -> (200, 270)."""
    m = _WIND_VAR_RE.match(token)
    if not m:
        return None
    lo, hi = int(m.group(1)), int(m.group(2))
    if lo > 360 or hi > 360:
        return None
    return lo, hi


def parse_visibility(tokens: List[str], i: int) -> Tuple[Optional[Visibility], int]:
    """Decode visibility starting at ``tokens[i]``.

    Returns ``(visibility, tokens_consumed)``. Handles ``9999``, ``0800NE``,
    ``////``, ``10SM``, ``P6SM``, ``M1/4SM`` and the two-token ``1 1/2SM``.
    """
    token = tokens[i]

    if token == "////":
        return Visibility(None), 1

    m = _VIS_METRIC_RE.match(token)
    if m:
        direction = m.group(2)
        if direction == "NDV":
            direction = None
        return Visibility(float(int(m.group(1))), "M", direction=direction), 1

    m = _VIS_SM_RE.match(token)
    if m:
        value = float(Fraction(m.group("val")))
        return Visibility(value, "SM", modifier=m.group("mod")), 1

    # Whole number followed by a fraction: "1 1/2SM", "2 3/4SM"
    if re.match(r"^\d$", token) and i + 1 < len(tokens):
        m = _VIS_SM_RE.match(tokens[i + 1])
        if m and "/" in m.group("val") and not m.group("mod"):
            value = int(token) + float(Fraction(m.group("val")))
            return Visibility(value, "SM"), 2

    return None, 0


def parse_rvr(token: str) -> Optional[RunwayVisualRange]:
    """``R24/0600U``, ``R10L/P1500N``, ``R27/1000V1800FT``."""
    m = _RVR_RE.match(token)
    if not m:
        return None
    return RunwayVisualRange(
        runway=m.group("rwy"),
        value=int(m.group("val")),
        unit="FT" if m.group("ft") else "M",
        modifier=m.group("mod"),
        variable_high=int(m.group("high")) if m.group("high") else None,
        variable_high_modifier=m.group("hmod"),
        tendency=m.group("tend"),
    )


def parse_weather(token: str) -> Optional[WeatherPhenomenon]:
    """``-SHRA``, ``+TSRAGR``, ``VCFG``, ``FZDZ``, ``RERA`` (recent), ``//``."""
    raw = token
    recent = False
    if token.startswith("RE") and len(token) > 2:
        recent = True
        token = token[2:]

    intensity = ""
    for prefix in INTENSITIES:
        if token.startswith(prefix):
            intensity = prefix
            token = token[len(prefix) :]
            break

    descriptor = None
    if token[:2] in DESCRIPTORS:
        descriptor = token[:2]
        token = token[2:]

    if len(token) % 2:
        return None
    codes = [token[j : j + 2] for j in range(0, len(token), 2)]
    if any(c not in PHENOMENA for c in codes):
        return None
    if not codes and descriptor is None:
        return None
    # "TS" and "SH" can stand alone; other descriptors need a phenomenon.
    if not codes and descriptor not in ("TS", "SH"):
        return None

    return WeatherPhenomenon(
        raw=raw,
        intensity=intensity,
        descriptor=descriptor,
        phenomena=codes,
        recent=recent,
    )


def parse_cloud(token: str) -> Optional[CloudLayer]:
    """``FEW020CB``, ``BKN035``, ``VV002``, ``SKC``, ``NSC``, ``//////``."""
    if token in CLEAR_SKY:
        return CloudLayer(cover=token)
    if token == "//////":
        return CloudLayer(cover="///")
    m = _CLOUD_RE.match(token)
    if not m:
        return None
    height = m.group("hgt")
    cloud_type = m.group("type")
    return CloudLayer(
        cover=m.group("cover"),
        height_ft=None if height == "///" else int(height) * 100,
        cloud_type=None if cloud_type in (None, "///") else cloud_type,
    )


def _signed(raw: Optional[str]) -> Optional[float]:
    if not raw or raw.startswith("/"):
        return None
    if raw.startswith("M"):
        return -float(raw[1:])
    return float(raw)


def parse_temperature(token: str) -> Optional[Tuple[Optional[float], Optional[float]]]:
    """``14/09`` -> (14.0, 9.0); ``M02/M05`` -> (-2.0, -5.0); ``M01/`` -> (-1.0, None)."""
    m = _TEMP_RE.match(token)
    if not m:
        return None
    return _signed(m.group("t")), _signed(m.group("d"))


def parse_pressure(token: str) -> Optional[Pressure]:
    """``Q1012`` -> 1012 hPa; ``A2992`` -> 29.92 inHg."""
    m = _PRESSURE_RE.match(token)
    if not m:
        return None
    value = int(m.group("val"))
    if m.group("unit") == "Q":
        return Pressure(float(value), "hPa")
    return Pressure(value / 100, "inHg")
