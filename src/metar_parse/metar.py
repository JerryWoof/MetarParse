"""METAR / SPECI parser.

``parse_metar`` splits a report into header, body, trend groups and remarks,
then tries the group decoders from :mod:`metar_parse.groups` on each token.
It is tolerant: tokens it cannot decode become :class:`ParseIssue` entries and
parsing continues.

Example::

    >>> m = parse_metar("METAR LKPR 121630Z 24012G22KT 9999 FEW020CB 14/09 Q1012 NOSIG")
    >>> m.station, m.wind.speed, m.pressure.hpa
    ('LKPR', 12, 1012.0)
"""

from __future__ import annotations

import re
from typing import List, Optional

from .groups import (
    parse_cloud,
    parse_day_time,
    parse_hhmm,
    parse_pressure,
    parse_rvr,
    parse_temperature,
    parse_visibility,
    parse_weather,
    parse_wind,
    parse_wind_variation,
)
from .models import Forecast, Metar, ParseIssue, TrendGroup

_CC_RE = re.compile(r"^CC[A-Z]$")
REPORT_TYPES = ("METAR", "SPECI")
TREND_KINDS = ("NOSIG", "BECMG", "TEMPO")

_STATION_RE = re.compile(r"^[A-Z][A-Z0-9]{3}$")
_TREND_TIME_RE = re.compile(r"^(FM|TL|AT)(\d{4})$")
# Runway state: R24/290050, R24L/CLRD70, R88/////// and the old 8-digit 88290050.
_RUNWAY_STATE_RE = re.compile(
    r"^(R\d{2}[LCR]?/(?:[\d/]{6}|CLRD[\d/]{2})|\d{2}(?:[\d/]{6}|CLRD[\d/]{2})|R?/?SNOCLO)$"
)
# Missing pressure: Q////, A////
_MISSING_PRESSURE_RE = re.compile(r"^[QA]/{4}$")
# Military airfield colour states, alone or combined: BLU, WHT+, BLACKYLO1, BLU+BLU+
_COLOR = r"(?:BLU|WHT|GRN|YLO[12]?|AMB|RED)\+?"
_COLOR_STATE_RE = re.compile(rf"^(?:BLACK)?{_COLOR}(?:{_COLOR})*$")
# Sea state (WMO): W12/S4 (state of the sea) or W18/H10 (wave height, dm)
_SEA_STATE_RE = re.compile(r"^W(?:M?\d{2}|//)/(?:S[\d/]|H[\d/]{1,3})$")
# Placeholders for groups the station could not report: ////SM, RE//, R22/////,
# and the US "M" / "MM" missing markers.
_MISSING_GROUP_RE = re.compile(r"^(?:/+(?:SM|KT|MPS)|RE/+|R\d{2}[LCR]?/+|MM?)$")


def _is_missing(token: str) -> bool:
    """``//``, ``////``, ``/////`` ...: a group the station could not report."""
    return bool(token) and set(token) == {"/"}


def _parse_windshear(tokens: List[str], i: int) -> int:
    """Number of tokens in a windshear group starting at ``tokens[i] == "WS"``.

    Handles ``WS R24``, ``WS RWY24``, ``WS ALL RWY`` and ``WS TKOF/LDG RWY24``.
    Returns 0 if the following tokens do not form a windshear group.
    """
    rest = tokens[i + 1 : i + 4]
    if rest[:2] == ["ALL", "RWY"]:
        return 3
    j = 0
    if rest and rest[0] in ("TKOF", "LDG"):
        j = 1
    if len(rest) > j and re.match(r"^(R|RWY)\d{2}[LCR]?$", rest[j]):
        return j + 2
    return 0


def _apply_forecast_token(fc: Forecast, tokens: List[str], i: int) -> int:
    """Decode a trend token into ``fc``; return tokens consumed (0 if unknown)."""
    token = tokens[i]
    if token == "CAVOK":
        fc.cavok = True
        return 1
    if token == "NSW":
        fc.nsw = True
        return 1
    wind = parse_wind(token)
    if wind is not None:
        fc.wind = wind
        return 1
    vis, used = parse_visibility(tokens, i)
    if used:
        if vis is not None and vis.value is not None:
            fc.visibility = vis
        return used
    weather = parse_weather(token)
    if weather is not None:
        fc.weather.append(weather)
        return 1
    cloud = parse_cloud(token)
    if cloud is not None:
        fc.clouds.append(cloud)
        return 1
    if _is_missing(token):
        return 1
    return 0


def _parse_trends(tokens: List[str], metar: Metar) -> None:
    """Decode ``NOSIG`` / ``BECMG ...`` / ``TEMPO ...`` groups (``tokens[0]`` is a kind)."""
    current: Optional[TrendGroup] = None
    group_tokens: List[str] = []

    def close() -> None:
        if current is not None:
            current.raw = " ".join(group_tokens)
            metar.trends.append(current)

    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token in TREND_KINDS:
            close()
            current = TrendGroup(kind=token)
            group_tokens = [token]
            i += 1
            continue
        if current is None:  # cannot happen: caller starts on a trend keyword
            metar.issues.append(ParseIssue(token, "unexpected token before trend group"))
            i += 1
            continue

        m = _TREND_TIME_RE.match(token)
        if m:
            time = parse_hhmm(m.group(2))
            if time is None:
                metar.issues.append(ParseIssue(token, "invalid trend time"))
            elif m.group(1) == "FM":
                current.from_time = time
            elif m.group(1) == "TL":
                current.until_time = time
            else:
                current.at_time = time
            group_tokens.append(token)
            i += 1
            continue

        used = _apply_forecast_token(current, tokens, i)
        if used:
            group_tokens.extend(tokens[i : i + used])
            i += used
            continue

        metar.issues.append(ParseIssue(token, "unrecognised trend group"))
        group_tokens.append(token)
        i += 1
    close()


def _parse_body(tokens: List[str], metar: Metar) -> int:
    """Decode the observation body; return the index where trend groups start."""
    i = 0
    n = len(tokens)
    while i < n:
        token = tokens[i]

        if token in TREND_KINDS:
            return i
        if token == "NIL":
            metar.nil = True
            return n
        if token == "AUTO":
            metar.auto = True
            i += 1
            continue
        if token == "COR" or _CC_RE.match(token):  # Canada: CCA, CCB ...
            metar.corrected = True
            i += 1
            continue
        if token == "CAVOK":
            metar.cavok = True
            i += 1
            continue

        wind = parse_wind(token)
        if wind is not None:
            if metar.wind is None:
                metar.wind = wind
            else:
                metar.issues.append(ParseIssue(token, "duplicate wind group"))
            i += 1
            continue

        variation = parse_wind_variation(token)
        if variation is not None:
            if metar.wind is not None:
                metar.wind.variable_from_deg, metar.wind.variable_to_deg = variation
            else:
                metar.issues.append(ParseIssue(token, "wind variation without wind group"))
            i += 1
            continue

        vis, used = parse_visibility(tokens, i)
        if used:
            if vis is None or vis.value is None:
                pass  # "////": visibility not reported
            elif metar.visibility is None:
                metar.visibility = vis
            elif (
                vis.unit == "M"
                and vis.direction
                and metar.min_visibility is None
                and metar.visibility.unit == "M"
            ):
                metar.min_visibility = vis  # e.g. "4000 1500SW"
            elif (
                vis.unit == "M"
                and metar.min_visibility is None
                and metar.visibility.unit == "M"
                and vis.value < metar.visibility.value  # type: ignore[operator]
            ):
                metar.min_visibility = vis  # minimum without direction: "9999 4900" (AUTO, NDV)
            else:
                metar.issues.append(ParseIssue(" ".join(tokens[i : i + used]), "extra visibility"))
            i += used
            continue

        rvr = parse_rvr(token)
        if rvr is not None:
            metar.runway_visual_range.append(rvr)
            i += 1
            continue

        weather = parse_weather(token)
        if weather is not None:
            if weather.recent:
                metar.recent_weather.append(weather)
            else:
                metar.weather.append(weather)
            i += 1
            continue

        cloud = parse_cloud(token)
        if cloud is not None:
            metar.clouds.append(cloud)
            i += 1
            continue

        temps = parse_temperature(token)
        if temps is not None:
            metar.temperature_c, metar.dewpoint_c = temps
            i += 1
            continue

        pressure = parse_pressure(token)
        if pressure is not None:
            if metar.pressure is None:  # Q and A both given: keep the first
                metar.pressure = pressure
            i += 1
            continue
        if _MISSING_PRESSURE_RE.match(token):
            i += 1
            continue

        if token == "WS":
            used = _parse_windshear(tokens, i)
            if used:
                metar.windshear.append(" ".join(tokens[i : i + used]))
                i += used
                continue

        if _RUNWAY_STATE_RE.match(token):
            metar.runway_state.append(token)
            i += 1
            continue

        if _COLOR_STATE_RE.match(token):
            metar.color_state = token
            i += 1
            continue

        if _SEA_STATE_RE.match(token):
            metar.sea_state = token
            i += 1
            continue

        if _is_missing(token) or _MISSING_GROUP_RE.match(token):
            i += 1
            continue

        metar.issues.append(ParseIssue(token, "unrecognised group"))
        i += 1
    return n


def _parse_header(tokens: List[str], metar: Metar) -> int:
    """Decode type, station, time, AUTO/COR/NIL; return the index of the body."""
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if i == 0 and token in REPORT_TYPES:
            metar.report_type = token
        elif token == "COR" or _CC_RE.match(token):
            metar.corrected = True
        elif token == "AUTO":
            metar.auto = True
        elif token == "NIL":
            metar.nil = True
            return len(tokens)
        elif metar.station is None and metar.time is None and _STATION_RE.match(token):
            metar.station = token
        elif metar.time is None and parse_day_time(token) is not None:
            metar.time = parse_day_time(token)
        else:
            break
        i += 1
    return i


def _normalise(raw: str) -> str:
    text = " ".join(str(raw or "").split())
    return text.rstrip("= ").strip()


def parse_metar(raw: str) -> Metar:
    """Decode a METAR or SPECI report. Never raises on malformed input.

    The leading ``METAR``/``SPECI`` keyword is optional; a trailing ``=`` is
    ignored. ``Metar.raw`` holds the whitespace-normalised report text.
    """
    text = _normalise(raw)
    metar = Metar(raw=text)
    tokens = text.split()
    if not tokens:
        metar.issues.append(ParseIssue("", "empty report"))
        return metar

    remarks_text: Optional[str] = None
    if "RMK" in tokens:
        k = tokens.index("RMK")
        remarks_text = " ".join(tokens[k + 1 :])
        tokens = tokens[:k]

    start = _parse_header(tokens, metar)
    if metar.station is None:
        metar.issues.append(ParseIssue(tokens[0], "missing station identifier"))

    if not metar.nil:
        body = tokens[start:]
        trend_start = _parse_body(body, metar)
        if not metar.nil and trend_start < len(body):
            _parse_trends(body[trend_start:], metar)

    if remarks_text is not None:
        from .remarks import parse_remarks

        try:
            metar.remarks = parse_remarks(remarks_text)
        except Exception as exc:  # keep the METAR usable if the remarks decoder fails
            metar.issues.append(ParseIssue(remarks_text, f"remarks decoding failed: {exc}"))
        else:
            _complete_remark_times(metar)
    return metar


def _complete_remark_times(metar: Metar) -> None:
    """Fill in the hour of minutes-only remark times (``PK WND 28045/55``, ``WSHFT 30``).

    Such events happened within the hour before the observation.
    """
    obs, rmk = metar.time, metar.remarks
    if obs is None or obs.hour is None or rmk is None:
        return
    events = [rmk.wind_shift, rmk.peak_wind.time if rmk.peak_wind else None]
    for t in events:
        if t is not None and t.hour is None:
            t.hour = obs.hour if t.minute <= obs.minute else (obs.hour - 1) % 24
