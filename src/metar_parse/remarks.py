"""Decoder for the METAR ``RMK`` section (US FMH-1 / ASOS conventions).

Only the well-defined coded groups are decoded; anything else (``RAB15E30``,
``CIG 005V010``, ``QFE754``, free text ...) is kept in ``Remarks.unparsed``
as the original tokens, in order. Decoding never raises.
"""

from __future__ import annotations

import re
from typing import List, Optional

from .models import DayTime, PeakWind, PressureTendency, Remarks

# Sensor status indicators (FMH-1 12.7.2). VISNO/CHINO may name a location.
SENSOR_STATUS = ("RVRNO", "PWINO", "PNO", "FZRANO", "TSNO", "VISNO", "CHINO", "SLPNO")
_SENSOR_WITH_LOCATION = ("VISNO", "CHINO")
_LOCATION_RE = re.compile(r"^RWY\d{2}[LCR]?$")

_LTG_FREQ = ("OCNL", "FRQ", "CONS")
_LTG_RE = re.compile(r"^LTG(?:IC|CG|CC|CA)*$")
_DIR = r"(?:N|NE|E|SE|S|SW|W|NW)"
_DIRECTION_RE = re.compile(rf"^{_DIR}(?:-{_DIR})*$")
_LTG_LOCATION_WORDS = ("DSNT", "VC", "OHD", "ALQDS", "ALQS")

_SLP_RE = re.compile(r"^SLP(\d{3})$")
_PRECISE_TEMP_RE = re.compile(r"^T([01])(\d{3})(?:([01])(\d{3}))?$")
_HOURLY_PRECIP_RE = re.compile(r"^P(\d{4}|////)$")
_PRECIP_3_6H_RE = re.compile(r"^6(\d{4}|////)$")
_PRECIP_24H_RE = re.compile(r"^7(\d{4}|////)$")
_MAX_6H_RE = re.compile(r"^1([01])(\d{3})$")
_MIN_6H_RE = re.compile(r"^2([01])(\d{3})$")
_EXTREMES_24H_RE = re.compile(r"^4([01])(\d{3})([01])(\d{3})$")
_TENDENCY_RE = re.compile(r"^5([0-8])(\d{3})$")
_SNOW_DEPTH_RE = re.compile(r"^4/(\d{3})$")
_SUNSHINE_RE = re.compile(r"^98(\d{3})$")
_PEAK_WIND_RE = re.compile(r"^(\d{3})(\d{2,3})/(\d{2})?(\d{2})$")
_TIME_RE = re.compile(r"^(\d{2})?(\d{2})$")


def _tenths(sign: str, digits: str) -> float:
    """``1``, ``025`` -> -2.5 (sign digit 1 means negative)."""
    value = int(digits) / 10
    return -value if sign == "1" else value


def _hundredths(raw: str) -> Optional[float]:
    """``0012`` -> 0.12 inches; ``////`` (indeterminate) -> None."""
    if raw.startswith("/"):
        return None
    return int(raw) / 100


def _time(hour: Optional[str], minute: str) -> Optional[DayTime]:
    """Remark time ``(hh)mm``. A missing hour is stored as ``-1``."""
    h = int(hour) if hour is not None else -1
    m = int(minute)
    if h > 24 or m > 59:
        return None
    return DayTime(None, h, m)


def _is_ltg_location(token: str) -> bool:
    return token in _LTG_LOCATION_WORDS or bool(_DIRECTION_RE.match(token))


def _lightning(tokens: List[str], i: int) -> int:
    """Length of a lightning phrase at ``tokens[i]`` (0 if none).

    ``LTG DSNT NE``, ``OCNL LTGICCG OHD``, ``FRQ LTGCGIC VC S-SW AND DSNT W``.
    """
    j = i
    if tokens[j] in _LTG_FREQ:
        j += 1
    if j >= len(tokens) or not _LTG_RE.match(tokens[j]):
        return 0
    j += 1
    while j < len(tokens):
        if _is_ltg_location(tokens[j]):
            j += 1
        elif tokens[j] == "AND" and j + 1 < len(tokens) and _is_ltg_location(tokens[j + 1]):
            j += 2
        else:
            break
    return j - i


def _decode_single(token: str, rmk: Remarks) -> bool:
    """Decode a self-contained group into ``rmk``. Returns False if unknown."""
    if token in ("AO1", "AO2"):
        rmk.station_type = token
        return True
    if token == "PRESRR":
        rmk.pressure_rising_rapidly = True
        return True
    if token == "PRESFR":
        rmk.pressure_falling_rapidly = True
        return True

    m = _SLP_RE.match(token)
    if m:
        ppp = int(m.group(1))
        base = 1000 if ppp < 500 else 900
        rmk.sea_level_pressure_hpa = round(base + ppp / 10, 1)
        return True

    m = _PRECISE_TEMP_RE.match(token)
    if m:
        rmk.precise_temperature_c = _tenths(m.group(1), m.group(2))
        if m.group(3):
            rmk.precise_dewpoint_c = _tenths(m.group(3), m.group(4))
        return True

    m = _HOURLY_PRECIP_RE.match(token)
    if m:
        rmk.hourly_precip_in = _hundredths(m.group(1))
        return True
    m = _PRECIP_3_6H_RE.match(token)
    if m:
        rmk.precip_3_6h_in = _hundredths(m.group(1))
        return True
    m = _PRECIP_24H_RE.match(token)
    if m:
        rmk.precip_24h_in = _hundredths(m.group(1))
        return True

    m = _MAX_6H_RE.match(token)
    if m:
        rmk.max_temp_6h_c = _tenths(m.group(1), m.group(2))
        return True
    m = _MIN_6H_RE.match(token)
    if m:
        rmk.min_temp_6h_c = _tenths(m.group(1), m.group(2))
        return True
    m = _EXTREMES_24H_RE.match(token)
    if m:
        rmk.max_temp_24h_c = _tenths(m.group(1), m.group(2))
        rmk.min_temp_24h_c = _tenths(m.group(3), m.group(4))
        return True

    m = _TENDENCY_RE.match(token)
    if m:
        rmk.pressure_tendency = PressureTendency(
            code=int(m.group(1)), change_hpa=int(m.group(2)) / 10
        )
        return True

    m = _SNOW_DEPTH_RE.match(token)
    if m:
        rmk.snow_depth_in = int(m.group(1))
        return True
    m = _SUNSHINE_RE.match(token)
    if m:
        rmk.sunshine_minutes = int(m.group(1))
        return True

    return False


def parse_remarks(text: str) -> Remarks:
    """Decode the text following ``RMK``.

    Example: ``AO2 PK WND 28045/1955 WSHFT 1940 FROPA SLP132 T01830061 $``.

    Times in ``PK WND`` and ``WSHFT`` are ``DayTime(None, hh, mm)``. When only
    minutes are reported (``PK WND 28045/55``, ``WSHFT 30``) the hour is
    implied by the observation time, which is unknown here, so ``hour`` is
    set to ``-1``.
    """
    rmk = Remarks(raw=text)
    tokens = (text or "").split()

    # Trailing "$" (maintenance indicator), possibly glued to the last group.
    if tokens and tokens[-1].endswith("$"):
        rmk.maintenance_needed = True
        tokens[-1] = tokens[-1][:-1]
        if not tokens[-1]:
            tokens.pop()

    i = 0
    n = len(tokens)
    while i < n:
        token = tokens[i]
        nxt = tokens[i + 1] if i + 1 < n else None

        # PK WND dddff(f)/(hh)mm
        if token == "PK" and nxt == "WND" and i + 2 < n:
            m = _PEAK_WIND_RE.match(tokens[i + 2])
            if m and int(m.group(1)) <= 360:
                time = _time(m.group(3), m.group(4))
                if time is not None:
                    rmk.peak_wind = PeakWind(
                        direction_deg=int(m.group(1)),
                        speed_kt=int(m.group(2)),
                        time=time,
                    )
                    i += 3
                    continue

        # WSHFT (hh)mm [FROPA]
        if token == "WSHFT" and nxt is not None:
            m = _TIME_RE.match(nxt)
            time = _time(m.group(1), m.group(2)) if m else None
            if time is not None:
                rmk.wind_shift = time
                i += 2
                if i < n and tokens[i] == "FROPA":
                    rmk.wind_shift_frontal = True
                    i += 1
                continue

        if token in SENSOR_STATUS:
            if (
                token in _SENSOR_WITH_LOCATION
                and nxt is not None
                and (_LOCATION_RE.match(nxt) or _DIRECTION_RE.match(nxt))
            ):
                rmk.sensor_status.append(f"{token} {nxt}")
                i += 2
            else:
                rmk.sensor_status.append(token)
                i += 1
            continue

        length = _lightning(tokens, i)
        if length:
            rmk.lightning.append(" ".join(tokens[i : i + length]))
            i += length
            continue

        if token == "$":
            rmk.maintenance_needed = True
        elif not _decode_single(token, rmk):
            rmk.unparsed.append(token)
        i += 1

    return rmk
