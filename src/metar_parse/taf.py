"""TAF (Terminal Aerodrome Forecast) parser.

A TAF is a header followed by a base forecast and any number of change
groups::

    TAF AMD LKPR 121100Z 1212/1312 24012KT 9999 FEW030
        TEMPO 1214/1218 4000 SHRA BKN020CB
        BECMG 1300/1302 VRB03KT
        PROB30 1304/1308 0800 FG
        TX18/1214Z TNM02/1305Z RMK ...

Parsing is tolerant: tokens that cannot be decoded become ``ParseIssue``
entries in ``Taf.issues``. Some recognised groups carry nothing the model can
hold and are skipped quietly: US low-level wind shear (``WS020/24035KT``) and
military altimeter forecasts (``QNH2992INS``).

``FM`` groups are self-contained forecasts that replace everything before
them, so each one's ``valid_to`` is set to the start of the next ``FM`` group,
or to the end of the TAF validity for the last one. ``BECMG``/``TEMPO``/
``PROB`` groups do not end an ``FM`` period.
"""

from __future__ import annotations

import re
from typing import List, Optional, Tuple

from .groups import (
    parse_cloud,
    parse_day_time,
    parse_period,
    parse_visibility,
    parse_weather,
    parse_wind,
    parse_wind_variation,
)
from .models import DayTime, ParseIssue, Taf, TafChangeGroup, TafTemperature

_STATION_RE = re.compile(r"^[A-Z][A-Z0-9]{3}$")
_FM_RE = re.compile(r"^FM(\d{2})?(\d{2})(\d{2})Z?$")  # FM121800, old FM1800
_PROB_RE = re.compile(r"^PROB(\d{2})$")
_OLD_PERIOD_RE = re.compile(r"^(\d{2})(\d{2})$")  # TEMPO 1418
_OLD_VALIDITY_RE = re.compile(r"^(\d{2})(\d{2})(\d{2})$")  # header 121212
_TEMP_RE = re.compile(r"^(?P<kind>TX|TN)(?P<neg>M)?(?P<t>\d{2})/(?P<day>\d{2})?(?P<hour>\d{2})Z$")
_WINDSHEAR_RE = re.compile(r"^WS\d{3}/\d{3}\d{2,3}(?:KT|MPS)$")
_QNH_RE = re.compile(r"^QNH\d{4}INS$")
_MILITARY_RE = re.compile(r"^(?:[56]\d{5}|FN\d{5})$")  # icing/turbulence, forecast number
# Amendment notes appended to US/military TAFs; stored with the remarks.
_AMENDMENT_NOTES = (("AMD", "NOT", "SKED"), ("AMD", "LTD", "TO"), ("LAST", "NO", "AMDS"))


def _is_group_start(token: str) -> bool:
    return token in ("BECMG", "TEMPO", "INTER") or bool(
        _FM_RE.match(token) or _PROB_RE.match(token)
    )


def _parse_fm(token: str) -> Optional[DayTime]:
    """``FM121800`` -> DayTime(12, 18, 0); old-style ``FM1800`` -> DayTime(None, 18, 0)."""
    m = _FM_RE.match(token)
    if not m:
        return None
    day = int(m.group(1)) if m.group(1) else None
    hour, minute = int(m.group(2)), int(m.group(3))
    if (day is not None and not 1 <= day <= 31) or hour > 24 or minute > 59:
        return None
    return DayTime(day, hour, minute)


def _parse_change_period(token: str) -> Optional[Tuple[DayTime, DayTime]]:
    """``1214/1218`` or old-style ``1418`` (hours only, no day)."""
    period = parse_period(token)
    if period:
        return period
    m = _OLD_PERIOD_RE.match(token)
    if m:
        h1, h2 = int(m.group(1)), int(m.group(2))
        if h1 <= 24 and h2 <= 24:
            return DayTime(None, h1), DayTime(None, h2)
    return None


def _parse_validity(token: str) -> Optional[Tuple[DayTime, DayTime]]:
    """Header validity: ``1212/1312`` or old-style ``121212`` (day, from hour, to hour)."""
    period = parse_period(token)
    if period:
        return period
    m = _OLD_VALIDITY_RE.match(token)
    if m:
        day, h1, h2 = (int(g) for g in m.groups())
        if 1 <= day <= 31 and h1 <= 24 and h2 <= 24:
            # The period crosses midnight when it ends at or before it starts.
            to_day = day if h2 > h1 else day % 31 + 1
            return DayTime(day, h1), DayTime(to_day, h2)
    return None


def _parse_temperature(token: str) -> Optional[TafTemperature]:
    """``TX25/1214Z`` -> TX 25 C at day 12 14Z; ``TNM02/1305Z`` -> -2 C."""
    m = _TEMP_RE.match(token)
    if not m:
        return None
    value = int(m.group("t"))
    hour = int(m.group("hour"))
    day = int(m.group("day")) if m.group("day") else None
    if hour > 24 or (day is not None and not 1 <= day <= 31):
        return None
    return TafTemperature(
        kind=m.group("kind"),
        temperature_c=-value if m.group("neg") else value,
        time=DayTime(day, hour),
    )


def _decode_body(tokens: List[str], group: TafChangeGroup, taf: Taf) -> None:
    """Decode the weather tokens of one group into ``group``."""
    i = 0
    while i < len(tokens):
        token = tokens[i]

        if token == "CAVOK":
            group.cavok = True
        elif token == "NSW":
            group.nsw = True
        elif group.wind is None and parse_wind(token):
            group.wind = parse_wind(token)
        elif group.wind is not None and parse_wind_variation(token):
            lo, hi = parse_wind_variation(token)  # type: ignore[misc]
            group.wind.variable_from_deg, group.wind.variable_to_deg = lo, hi
        elif _parse_temperature(token):
            taf.temperatures.append(_parse_temperature(token))  # type: ignore[arg-type]
        elif _WINDSHEAR_RE.match(token) or _QNH_RE.match(token) or _MILITARY_RE.match(token):
            pass  # recognised, but not represented in the model
        elif parse_weather(token):
            group.weather.append(parse_weather(token))  # type: ignore[arg-type]
        elif parse_cloud(token):
            group.clouds.append(parse_cloud(token))  # type: ignore[arg-type]
        else:
            vis, used = parse_visibility(tokens, i) if group.visibility is None else (None, 0)
            if vis is not None:
                group.visibility = vis
                i += used
                continue
            taf.issues.append(ParseIssue(token, "unrecognised TAF group"))
        i += 1


def _split_groups(tokens: List[str]) -> List[List[str]]:
    """Split the body into the base forecast and one token list per change group."""
    chunks: List[List[str]] = [[]]
    for j, token in enumerate(tokens):
        # "PROB40 TEMPO" is a single group: don't split at the TEMPO.
        joined_prob = token in ("TEMPO", "INTER") and j > 0 and _PROB_RE.match(tokens[j - 1])
        if _is_group_start(token) and not joined_prob:
            chunks.append([])
        chunks[-1].append(token)
    return chunks


def _build_change_group(chunk: List[str], taf: Taf) -> TafChangeGroup:
    group = TafChangeGroup(raw=" ".join(chunk))
    head, rest = chunk[0], chunk[1:]

    fm = _parse_fm(head)
    if fm is not None or _FM_RE.match(head):
        group.kind = "FM"
        group.valid_from = fm
        if fm is None:
            taf.issues.append(ParseIssue(head, "invalid FM time"))
    else:
        prob = _PROB_RE.match(head)
        if prob:
            group.probability = int(prob.group(1))
            group.kind = "PROB"
            if rest and rest[0] in ("TEMPO", "INTER"):
                group.kind = rest[0]
                rest = rest[1:]
        else:
            group.kind = head  # BECMG, TEMPO or INTER (Australia)
        period = _parse_change_period(rest[0]) if rest else None
        if period:
            group.valid_from, group.valid_to = period
            rest = rest[1:]
        else:
            taf.issues.append(ParseIssue(group.raw, f"{group.kind} group without a valid period"))

    _decode_body(rest, group, taf)
    return group


def _extract_amendment_notes(tokens: List[str], taf: Taf) -> List[str]:
    """Move ``AMD NOT SKED`` / ``LAST NO AMDS AFT 1115 NEXT 1307`` phrases to remarks_raw.

    A note runs until the next TX/TN temperature or change group.
    """
    for k in range(1, len(tokens)):
        if tuple(tokens[k : k + 3]) in _AMENDMENT_NOTES:
            end = k + 3
            while end < len(tokens) and not (
                _parse_temperature(tokens[end]) or _is_group_start(tokens[end])
            ):
                end += 1
            note = " ".join(tokens[k:end])
            taf.remarks_raw = f"{note} {taf.remarks_raw}" if taf.remarks_raw else note
            return tokens[:k] + tokens[end:]
    return tokens


def parse_taf(raw: str) -> Taf:
    """Decode a TAF into a :class:`Taf`. Never raises on malformed input.

    >>> taf = parse_taf("TAF LKPR 121100Z 1212/1312 24012KT 9999 FEW030 TEMPO 1214/1218 4000 SHRA")
    >>> [g.kind for g in taf.groups]
    ['BASE', 'TEMPO']
    """
    taf = Taf(raw=raw)
    text = " ".join(raw.split()).rstrip("=").strip()
    if not text:
        taf.issues.append(ParseIssue("", "empty report"))
        return taf

    # Split off remarks first: everything after RMK is free text.
    tokens = text.split(" ")
    if "RMK" in tokens:
        k = tokens.index("RMK")
        taf.remarks_raw = " ".join(tokens[k + 1 :])
        tokens = tokens[:k]
    tokens = [t.rstrip("=") for t in tokens if t.rstrip("=")]
    tokens = _extract_amendment_notes(tokens, taf)

    # --- header ---
    i = 0
    if i < len(tokens) and tokens[i] == "TAF":
        i += 1
    while i < len(tokens) and tokens[i] in ("AMD", "COR"):
        if tokens[i] == "AMD":
            taf.amended = True
        else:
            taf.corrected = True
        i += 1
    if i < len(tokens) and _STATION_RE.match(tokens[i]) and not _is_group_start(tokens[i]):
        taf.station = tokens[i]
        i += 1
    if i < len(tokens):
        issued = parse_day_time(tokens[i])
        if issued:
            taf.issued = issued
            i += 1
    if i < len(tokens) and tokens[i] == "NIL":
        taf.nil = True
        i += 1
    if i < len(tokens):
        validity = _parse_validity(tokens[i])
        if validity:
            taf.valid_from, taf.valid_to = validity
            i += 1
    if i < len(tokens) and tokens[i] == "CNL":
        taf.cancelled = True
        i += 1
    if taf.nil:
        taf.issues.extend(ParseIssue(t, "unexpected token after NIL") for t in tokens[i:])
        return taf

    # TX/TN extremes belong to the whole TAF, not to the group they follow.
    body = []
    for token in tokens[i:]:
        temperature = _parse_temperature(token)
        if temperature is not None:
            taf.temperatures.append(temperature)
        else:
            body.append(token)
    if not body:
        return taf

    # --- base forecast and change groups ---
    chunks = _split_groups(body)
    base = TafChangeGroup(
        kind="BASE",
        valid_from=taf.valid_from,
        valid_to=taf.valid_to,
        raw=" ".join(chunks[0]),
    )
    _decode_body(chunks[0], base, taf)
    taf.groups.append(base)
    taf.groups.extend(_build_change_group(chunk, taf) for chunk in chunks[1:])

    # FM periods run until the next FM group, or the end of the TAF.
    fm_groups = [g for g in taf.groups if g.kind == "FM"]
    for current, nxt in zip(fm_groups, fm_groups[1:] + [None]):
        current.valid_to = nxt.valid_from if nxt else taf.valid_to

    return taf
