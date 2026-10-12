"""``metar`` command-line tool: fetch and decode live METARs/TAFs.

Examples::

    metar LKPR EGLL              # latest METARs, human-readable
    metar --taf KJFK             # TAFs
    metar --json --hours 3 LKPR  # 3 hours of history as JSON
    metar --raw "METAR LKPR 121630Z 24012G22KT 9999 FEW020 14/09 Q1012"
    cat reports.txt | metar -    # decode reports from stdin, one per line
"""

from __future__ import annotations

import argparse
import json
import sys
from fractions import Fraction
from typing import Any, List, Optional, Sequence, TextIO

from . import __version__
from .fetch import FetchError, fetch_metars, fetch_tafs
from .metar import parse_metar
from .models import (
    CloudLayer,
    DayTime,
    Forecast,
    Metar,
    Taf,
    Visibility,
    WeatherPhenomenon,
    Wind,
)
from .taf import parse_taf

_INTENSITY = {"-": "light", "+": "heavy", "VC": "in the vicinity:"}
_DESCRIPTOR = {
    "MI": "shallow",
    "BC": "patches of",
    "PR": "partial",
    "DR": "low drifting",
    "BL": "blowing",
    "SH": "showers of",
    "TS": "thunderstorm with",
    "FZ": "freezing",
}
_PHENOMENON = {
    "DZ": "drizzle",
    "RA": "rain",
    "SN": "snow",
    "SG": "snow grains",
    "IC": "ice crystals",
    "PL": "ice pellets",
    "GR": "hail",
    "GS": "small hail",
    "UP": "unknown precipitation",
    "BR": "mist",
    "FG": "fog",
    "FU": "smoke",
    "VA": "volcanic ash",
    "DU": "dust",
    "SA": "sand",
    "HZ": "haze",
    "PY": "spray",
    "PO": "dust whirls",
    "SQ": "squalls",
    "FC": "funnel cloud",
    "SS": "sandstorm",
    "DS": "duststorm",
}
_COVER = {
    "FEW": "few",
    "SCT": "scattered",
    "BKN": "broken",
    "OVC": "overcast",
    "VV": "vertical visibility",
    "SKC": "sky clear",
    "CLR": "clear",
    "NSC": "no significant cloud",
    "NCD": "no cloud detected",
    "///": "unknown",
}

LABEL_WIDTH = 11


# --------------------------------------------------------------------------- formatting


def _fmt_time(t: Optional[DayTime]) -> str:
    if t is None:
        return "?"
    day = f"day {t.day} " if t.day is not None else ""
    return f"{day}{t.hour:02d}:{t.minute:02d}Z"


def _fmt_num(value: float) -> str:
    return f"{value:g}"


def fmt_wind(wind: Optional[Wind]) -> Optional[str]:
    if wind is None:
        return None
    if wind.speed is None:
        return "not reported"
    if wind.calm:
        return "calm"
    unit = {"KT": "kt", "MPS": "m/s", "KMH": "km/h"}.get(wind.unit, wind.unit.lower())
    if wind.variable:
        direction = "variable"
    elif wind.direction_deg is None:
        direction = "?"
    else:
        direction = f"{wind.direction_deg:03d}°"
    text = f"{direction} {wind.speed} {unit}"
    if wind.gust is not None:
        text += f", gusts {wind.gust} {unit}"
    if wind.variable_from_deg is not None and wind.variable_to_deg is not None:
        text += f" (varying {wind.variable_from_deg:03d}°–{wind.variable_to_deg:03d}°)"
    return text


def _fmt_miles(value: float) -> str:
    whole = int(value)
    frac = Fraction(value - whole).limit_denominator(16)
    if frac == 0:
        return str(whole)
    return f"{whole} {frac}" if whole else str(frac)


def fmt_visibility(vis: Optional[Visibility], cavok: bool = False) -> Optional[str]:
    if cavok:
        return "CAVOK (10 km or more, no cloud below 5000 ft)"
    if vis is None:
        return None
    if vis.value is None:
        return "not reported"
    prefix = {"P": "more than ", "M": "less than "}.get(vis.modifier or "", "")
    if vis.unit == "SM":
        text = f"{prefix}{_fmt_miles(vis.value)} SM"
    elif vis.value >= 9999:
        text = "10 km or more"
    elif vis.value >= 5000:
        text = f"{prefix}{_fmt_num(vis.value / 1000)} km"
    else:
        text = f"{prefix}{int(vis.value)} m"
    if vis.direction:
        text += f" to the {vis.direction}"
    return text


def fmt_weather(wx: WeatherPhenomenon) -> str:
    words: List[str] = []
    if wx.recent:
        words.append("recent")
    if wx.intensity:
        words.append(_INTENSITY.get(wx.intensity, wx.intensity))
    if wx.descriptor:
        if wx.phenomena:
            words.append(_DESCRIPTOR.get(wx.descriptor, wx.descriptor))
        else:
            words.append({"TS": "thunderstorm", "SH": "showers"}.get(wx.descriptor, wx.descriptor))
    words.append(" and ".join(_PHENOMENON.get(p, p) for p in wx.phenomena))
    return " ".join(w for w in words if w)


def fmt_cloud(layer: CloudLayer) -> str:
    text = _COVER.get(layer.cover, layer.cover)
    if layer.height_ft is not None:
        text += f" {layer.height_ft:,} ft"
    if layer.cloud_type:
        text += f" {layer.cloud_type}"
    return text


def _fmt_forecast(fc: Forecast) -> List[str]:
    """Compact pieces describing a trend or TAF period."""
    parts: List[str] = []
    wind = fmt_wind(fc.wind)
    if wind:
        parts.append(f"wind {wind}")
    vis = fmt_visibility(fc.visibility, fc.cavok)
    if vis:
        parts.append("CAVOK" if fc.cavok else f"vis {vis}")
    parts.extend(fmt_weather(w) for w in fc.weather)
    if fc.nsw:
        parts.append("no significant weather")
    if fc.clouds:
        parts.append(", ".join(fmt_cloud(c) for c in fc.clouds))
    return parts


def _row(label: str, value: str) -> str:
    return f"  {label:<{LABEL_WIDTH}} {value}"


def format_metar(m: Metar) -> str:
    station = m.station or "????"
    header = f"{station}  {m.report_type} {_fmt_time(m.time)}"
    flags = [f for f, on in (("AUTO", m.auto), ("COR", m.corrected)) if on]
    if flags:
        header += "  " + " ".join(flags)
    category = m.flight_category
    if category:
        header += f"  [{category}]"
    lines = [header]
    if m.nil:
        lines.append(_row("Report", "NIL (missing)"))

    wind = fmt_wind(m.wind)
    if wind:
        lines.append(_row("Wind", wind))
    vis = fmt_visibility(m.visibility, m.cavok)
    if vis:
        if m.min_visibility is not None:
            vis += f", min {fmt_visibility(m.min_visibility)}"
        lines.append(_row("Visibility", vis))
    if m.weather:
        lines.append(_row("Weather", ", ".join(fmt_weather(w) for w in m.weather)))
    if m.recent_weather:
        lines.append(_row("Recent", ", ".join(fmt_weather(w) for w in m.recent_weather)))
    if m.clouds:
        clouds = ", ".join(fmt_cloud(c) for c in m.clouds)
        if m.ceiling_ft is not None:
            clouds += f"  (ceiling {m.ceiling_ft:,} ft)"
        lines.append(_row("Clouds", clouds))
    if m.temperature_c is not None:
        temp = f"{_fmt_num(m.temperature_c)} °C"
        if m.dewpoint_c is not None:
            temp += f" / dew point {_fmt_num(m.dewpoint_c)} °C"
        rh = m.relative_humidity
        if rh is not None:
            temp += f"  (RH {rh:g} %)"
        lines.append(_row("Temp", temp))
    if m.pressure is not None:
        if m.pressure.unit == "inHg":
            pressure = f"{m.pressure.inhg:.2f} inHg ({m.pressure.hpa:g} hPa)"
        else:
            pressure = f"{m.pressure.hpa:g} hPa ({m.pressure.inhg:.2f} inHg)"
        lines.append(_row("Pressure", pressure))
    for trend in m.trends:
        desc = ", ".join(_fmt_forecast(trend))
        lines.append(_row("Trend", f"{trend.kind} {desc}".strip()))
    if m.issues:
        lines.append(_row("Unparsed", " ".join(i.token for i in m.issues)))
    lines.append(_row("Raw", m.raw))
    return "\n".join(lines)


def _fmt_period(start: Optional[DayTime], end: Optional[DayTime]) -> str:
    if start is None:
        return ""
    if end is None:
        return _fmt_time(start)
    return f"{_fmt_time(start)} – {_fmt_time(end)}"


def format_taf(t: Taf) -> str:
    station = t.station or "????"
    header = f"{station}  TAF issued {_fmt_time(t.issued)}"
    if t.valid_from is not None:
        header += f", valid {_fmt_period(t.valid_from, t.valid_to)}"
    flags = [f for f, on in (("AMD", t.amended), ("COR", t.corrected)) if on]
    if flags:
        header += "  " + " ".join(flags)
    lines = [header]
    if t.nil:
        lines.append(_row("Report", "NIL (missing)"))
    if t.cancelled:
        lines.append(_row("Report", "cancelled"))
    for group in t.groups:
        label = group.kind
        if group.probability is not None:
            label = f"PROB{group.probability}" + (" TEMPO" if group.kind == "TEMPO" else "")
        period = _fmt_period(group.valid_from, group.valid_to)
        desc = ", ".join(_fmt_forecast(group))
        lines.append(_row(label, f"{period}: {desc}" if period else desc))
    for temp in t.temperatures:
        kind = "max" if temp.kind == "TX" else "min"
        lines.append(_row("Temp", f"{kind} {temp.temperature_c} °C at {_fmt_time(temp.time)}"))
    if t.issues:
        lines.append(_row("Unparsed", " ".join(i.token for i in t.issues)))
    lines.append(_row("Raw", t.raw))
    return "\n".join(lines)


def report_to_dict(report: Any) -> dict:
    data = report.to_dict()
    if isinstance(report, Metar):
        data["flight_category"] = report.flight_category
    return data


# --------------------------------------------------------------------------- CLI


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="metar",
        description="Fetch and decode METAR/TAF reports (live data from aviationweather.gov).",
        epilog="Exit codes: 0 ok, 1 fetch error or no reports, 2 usage error.",
    )
    parser.add_argument(
        "stations",
        nargs="*",
        metavar="STATION",
        help="ICAO station IDs (e.g. LKPR EGLL); '-' reads reports from stdin",
    )
    parser.add_argument("--taf", action="store_true", help="fetch/decode TAFs instead of METARs")
    parser.add_argument("--json", action="store_true", help="print decoded reports as JSON")
    parser.add_argument(
        "--raw",
        metavar="REPORT",
        action="append",
        help="decode the given report text without network access (repeatable)",
    )
    parser.add_argument("--hours", type=float, metavar="N", help="include N hours of METAR history")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def _is_taf(text: str, force_taf: bool) -> bool:
    return force_taf or text.lstrip().upper().startswith("TAF")


def _read_lines(stream: TextIO) -> List[str]:
    return [line.strip() for line in stream if line.strip()]


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:  # --help, --version, usage errors
        return exc.code if isinstance(exc.code, int) else 2

    def usage_error(message: str) -> int:
        parser.print_usage(sys.stderr)
        print(f"metar: error: {message}", file=sys.stderr)
        return 2

    stations: List[str] = args.stations
    use_stdin = stations == ["-"] or (not stations and not args.raw and not sys.stdin.isatty())
    if "-" in stations and stations != ["-"]:
        return usage_error("'-' cannot be combined with station IDs")
    if args.raw and stations:
        return usage_error("--raw cannot be combined with station IDs")
    if args.hours is not None and args.taf:
        return usage_error("--hours applies to METARs only")

    if args.raw:
        texts, fetched_tafs = args.raw, False
    elif use_stdin:
        texts, fetched_tafs = _read_lines(sys.stdin), False
    elif not stations:
        return usage_error("give station IDs, --raw REPORT, or pipe reports on stdin")
    else:
        try:
            if args.taf:
                texts = fetch_tafs(stations)
            else:
                texts = fetch_metars(stations, hours=args.hours)
        except ValueError as exc:
            return usage_error(str(exc))
        except FetchError as exc:
            print(f"metar: {exc}", file=sys.stderr)
            return 1
        fetched_tafs = args.taf

    if not texts:
        print("metar: no reports found", file=sys.stderr)
        return 1

    reports = [
        parse_taf(t) if fetched_tafs or _is_taf(t, args.taf) else parse_metar(t) for t in texts
    ]

    if args.json:
        print(json.dumps([report_to_dict(r) for r in reports], indent=2, ensure_ascii=False))
    else:
        blocks = [format_taf(r) if isinstance(r, Taf) else format_metar(r) for r in reports]
        print("\n\n".join(blocks))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
