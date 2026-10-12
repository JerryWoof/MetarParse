#!/usr/bin/env python3
"""Build the static JSON data for the MetarParse Live map (``site/data/``).

Downloads the aviationweather.gov bulk caches (latest METARs, current TAFs and
station metadata), decodes every raw report with MetarParse and writes
``metars.json``, ``tafs.json`` and ``meta.json`` as described in
``site/DATA_FORMAT.md``.

The AWC columns are only used for station coordinates and times when the
report itself cannot provide them, and for the ``category_agreement`` metric
that compares our flight category with AWC's.

Usage::

    python scripts/build_site_data.py --out site/data
    python scripts/build_site_data.py --from-dir /path/to/caches   # offline

``--from-dir`` reads ``metars.cache.csv.gz``, ``tafs.cache.xml.gz`` and
``stations.cache.json.gz`` from a local directory instead of downloading.
Exit codes: 0 ok, 1 download failure or no usable METARs.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import io
import json
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple

from metar_parse import __version__
from metar_parse.cli import fmt_weather
from metar_parse.derived import flight_category, relative_humidity
from metar_parse.fetch import USER_AGENT
from metar_parse.metar import parse_metar
from metar_parse.models import DayTime, Forecast, Metar, WeatherPhenomenon
from metar_parse.taf import parse_taf

CACHE_URL = "https://aviationweather.gov/data/cache/"
METARS_FILE = "metars.cache.csv.gz"
TAFS_FILE = "tafs.cache.xml.gz"
STATIONS_FILE = "stations.cache.json.gz"
CACHE_FILES = (METARS_FILE, TAFS_FILE, STATIONS_FILE)

CATEGORIES = ("VFR", "MVFR", "IFR", "LIFR")


class BuildError(Exception):
    """Raised when the source data cannot be obtained."""


# --------------------------------------------------------------------------- input


def download(name: str, timeout: float = 60, attempts: int = 3) -> bytes:
    """Download one cache file, retrying a few times on network errors."""
    url = CACHE_URL + name
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    last: Optional[Exception] = None
    for attempt in range(attempts):
        if attempt:
            time.sleep(5 * attempt)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read()
        except (urllib.error.URLError, OSError) as exc:
            last = exc
    raise BuildError(f"cannot download {url}: {last}")


def load_caches(from_dir: Optional[Path]) -> Dict[str, bytes]:
    """Raw (decompressed) contents of the three cache files, keyed by file name."""
    out: Dict[str, bytes] = {}
    for name in CACHE_FILES:
        if from_dir is not None:
            path = from_dir / name
            try:
                blob = path.read_bytes()
            except OSError as exc:
                raise BuildError(f"cannot read {path}: {exc}") from exc
        else:
            blob = download(name)
        try:
            out[name] = gzip.decompress(blob)
        except (OSError, EOFError) as exc:
            raise BuildError(f"{name} is not valid gzip data: {exc}") from exc
    return out


def parse_time(text: Optional[str]) -> Optional[datetime]:
    """Parse an AWC ISO time such as ``2026-10-12T15:10:00.000Z``."""
    if not text:
        return None
    text = text.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        value = datetime.fromisoformat(text)
    except ValueError:
        try:  # Python 3.9/3.10 reject some fractional-second forms
            value = datetime.strptime(text[:19], "%Y-%m-%dT%H:%M:%S")
        except ValueError:
            return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def iso(value: Optional[datetime]) -> Optional[str]:
    return value.strftime("%Y-%m-%dT%H:%M:%SZ") if value is not None else None


def _float(text: Any) -> Optional[float]:
    try:
        value = float(text)
    except (TypeError, ValueError):
        return None
    return value if value == value else None  # drop NaN


def valid_coords(lat: Optional[float], lon: Optional[float]) -> bool:
    """AWC uses -99.99 as a placeholder for unknown coordinates."""
    if lat is None or lon is None:
        return False
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return False
    return not (lat == -99.99 or lon == -99.99)


def read_metar_rows(data: bytes) -> Iterator[Dict[str, str]]:
    """Rows of the METAR CSV. The header has duplicate columns; first one wins."""
    reader = csv.reader(io.StringIO(data.decode("utf-8", errors="replace")))
    header = next(reader, None)
    if not header:
        return
    index: Dict[str, int] = {}
    for i, name in enumerate(header):
        index.setdefault(name.strip(), i)
    for row in reader:
        yield {name: row[i] if i < len(row) else "" for name, i in index.items()}


def read_tafs(data: bytes) -> Iterator[Tuple[str, str, Optional[datetime]]]:
    """``(station_id, raw_text, issue_time)`` for every ``<TAF>`` element."""
    root = ET.fromstring(data)
    for node in root.iter("TAF"):
        raw = " ".join((node.findtext("raw_text") or "").split())
        station = (node.findtext("station_id") or "").strip().upper()
        yield station, raw, parse_time(node.findtext("issue_time"))


def read_stations(data: bytes) -> Dict[str, Dict[str, Any]]:
    """Station metadata keyed by ICAO id (falling back to the AWC ``id``)."""
    out: Dict[str, Dict[str, Any]] = {}
    for item in json.loads(data.decode("utf-8", errors="replace")):
        if not isinstance(item, dict):
            continue
        for key in (item.get("id"), item.get("icaoId")):  # icaoId overrides id
            if isinstance(key, str) and key:
                out[key.upper()] = item
    return out


# --------------------------------------------------------------------------- encoding


def _r1(value: Optional[float]) -> Optional[float]:
    return round(float(value), 1) if value is not None else None


def _int(value: Optional[float]) -> Optional[int]:
    return int(round(value)) if value is not None else None


def _resolve(dt: Optional[DayTime], reference: Optional[datetime]) -> Optional[datetime]:
    if dt is None or reference is None:
        return None
    return dt.to_datetime(reference)


def _wx(weather: List[WeatherPhenomenon]) -> Tuple[List[str], str]:
    return [w.raw for w in weather], ", ".join(fmt_weather(w) for w in weather)


def _clouds(fc: Any) -> List[list]:
    return [[c.cover, c.height_ft, c.cloud_type] for c in fc.clouds]


def _wind_fields(fc: Any) -> Dict[str, Any]:
    wind = fc.wind
    if wind is None:
        return {"wdir": None, "wspd": None, "gust": None, "vrb": False}
    return {
        "wdir": None if wind.variable else wind.direction_deg,
        "wspd": _int(wind.speed_kt),
        "gust": _int(wind.gust_kt),
        "vrb": wind.variable,
    }


def _vis_m(fc: Any) -> Optional[float]:
    if fc.visibility is not None and fc.visibility.meters is not None:
        return _r1(fc.visibility.meters)
    if fc.cavok:
        return 9999.0
    return None


def encode_metar(
    m: Metar, row: Dict[str, str], station: Optional[Dict[str, Any]], obs_time: Optional[datetime]
) -> Optional[Dict[str, Any]]:
    """One ``metars.json`` entry, or ``None`` if the station has no coordinates."""
    station = station or {}
    lat, lon = _float(station.get("lat")), _float(station.get("lon"))
    if not valid_coords(lat, lon):
        lat, lon = _float(row.get("latitude")), _float(row.get("longitude"))
    if not valid_coords(lat, lon):
        return None
    elev = _float(station.get("elev"))
    if elev is None:
        elev = _float(row.get("elevation_m"))
    if elev is not None and elev == 9999:
        elev = None

    when = _resolve(m.time, obs_time) or obs_time
    wind = _wind_fields(m)
    wvar = None
    if m.wind is not None and m.wind.variable_from_deg is not None:
        wvar = [m.wind.variable_from_deg, m.wind.variable_to_deg]
    wx, wx_text = _wx(m.weather)
    return {
        "id": m.station or row.get("station_id", "").strip().upper(),
        "iata": station.get("iataId") or None,
        "name": station.get("site") or None,
        "country": station.get("country") or None,
        "lat": round(lat, 3),
        "lon": round(lon, 3),
        "elev_m": _int(elev),
        "time": iso(when),
        "type": m.report_type,
        "cat": m.flight_category,
        "temp": _r1(m.temperature_c),
        "dew": _r1(m.dewpoint_c),
        "rh": relative_humidity(m.temperature_c, m.dewpoint_c),
        "wdir": wind["wdir"],
        "wspd": wind["wspd"],
        "gust": wind["gust"],
        "wvar": wvar,
        "vrb": wind["vrb"],
        "vis_m": _vis_m(m),
        "ceil_ft": m.ceiling_ft,
        "cavok": m.cavok,
        "clouds": _clouds(m),
        "wx": wx,
        "wx_text": wx_text,
        "qnh": _r1(m.pressure.hpa) if m.pressure is not None else None,
        "raw": m.raw,
        "issues": [f"{i.token}: {i.message}" for i in m.issues],
        "taf": False,
    }


def group_category(fc: Forecast) -> Optional[str]:
    """Flight category of a TAF period, or ``None`` if it states neither vis nor sky."""
    if fc.visibility is None and not fc.clouds and not fc.cavok:
        return None
    probe = Metar(raw="", visibility=fc.visibility, clouds=list(fc.clouds), cavok=fc.cavok)
    return flight_category(probe)


def encode_taf(raw: str, issue_time: Optional[datetime]) -> Dict[str, Any]:
    taf = parse_taf(raw)
    issued = _resolve(taf.issued, issue_time) or issue_time
    start = _resolve(taf.valid_from, issued)
    end = _resolve(taf.valid_to, start or issued)
    groups = []
    for g in taf.groups:
        g_from = _resolve(g.valid_from, start or issued)
        g_to = _resolve(g.valid_to, g_from or start or issued)
        wx, wx_text = _wx(g.weather)
        entry: Dict[str, Any] = {
            "kind": g.kind,
            "prob": g.probability,
            "from": iso(g_from),
            "to": iso(g_to),
            "cat": group_category(g),
        }
        entry.update(_wind_fields(g))
        entry.update(
            {
                "vis_m": _vis_m(g),
                "ceil_ft": _ceiling(g),
                "cavok": g.cavok,
                "clouds": _clouds(g),
                "wx": wx,
                "wx_text": wx_text,
                "raw": g.raw,
            }
        )
        groups.append(entry)
    return {
        "raw": taf.raw,
        "issued": iso(issued),
        "from": iso(start),
        "to": iso(end),
        "groups": groups,
    }


def _ceiling(fc: Forecast) -> Optional[int]:
    heights = [c.height_ft for c in fc.clouds if c.is_ceiling and c.height_ft is not None]
    return min(heights) if heights else None


# --------------------------------------------------------------------------- build


def build(caches: Dict[str, bytes]) -> Tuple[list, dict, dict, Counter]:
    """Decode everything; return ``(metars, tafs, meta, issue_token_counts)``."""
    stations = read_stations(caches[STATIONS_FILE])

    # Latest METAR per station (by AWC observation time).
    latest: Dict[str, Tuple[Optional[datetime], Dict[str, str]]] = {}
    for row in read_metar_rows(caches[METARS_FILE]):
        try:
            sid = (row.get("station_id") or "").strip().upper()
            raw = (row.get("raw_text") or "").strip()
            if not sid or not raw:
                continue
            obs = parse_time(row.get("observation_time"))
            prev = latest.get(sid)
            if prev is None or (obs is not None and (prev[0] is None or obs > prev[0])):
                latest[sid] = (obs, row)
        except Exception as exc:  # never let one bad row stop the build
            print(f"skipping METAR row: {exc}", file=sys.stderr)

    metars: List[Dict[str, Any]] = []
    issue_tokens: Counter = Counter()
    compared = agreed = 0
    for sid in sorted(latest):
        obs, row = latest[sid]
        try:
            m = parse_metar(row["raw_text"])
            entry = encode_metar(m, row, stations.get(sid), obs)
        except Exception as exc:
            print(f"skipping METAR {sid}: {exc}", file=sys.stderr)
            continue
        if entry is None:
            continue
        entry["id"] = sid
        metars.append(entry)
        issue_tokens.update(i.token for i in m.issues)
        awc_cat = (row.get("flight_category") or "").strip().upper()
        if awc_cat in CATEGORIES:
            compared += 1
            agreed += entry["cat"] == awc_cat

    # Latest TAF per station that is on the map.
    on_map = {m["id"]: m for m in metars}
    latest_taf: Dict[str, Tuple[Optional[datetime], str]] = {}
    for sid, raw, issued in read_tafs(caches[TAFS_FILE]):
        if sid not in on_map or not raw:
            continue
        prev = latest_taf.get(sid)
        if prev is None or (issued is not None and (prev[0] is None or issued > prev[0])):
            latest_taf[sid] = (issued, raw)
    tafs: Dict[str, Any] = {}
    for sid in sorted(latest_taf):
        issued, raw = latest_taf[sid]
        try:
            tafs[sid] = encode_taf(raw, issued)
        except Exception as exc:
            print(f"skipping TAF {sid}: {exc}", file=sys.stderr)
            continue
        on_map[sid]["taf"] = True

    clean = sum(1 for m in metars if not m["issues"])
    meta = {
        "generated": iso(datetime.now(timezone.utc)),
        "library_version": __version__,
        "metar_count": len(metars),
        "taf_count": len(tafs),
        "clean_rate": round(clean / len(metars), 4) if metars else 0.0,
        "category_agreement": round(agreed / compared, 4) if compared else 0.0,
        "category_compared": compared,
        "source": "aviationweather.gov",
        "wheel": f"pkg/metar_parse-{__version__}-py3-none-any.whl",
    }
    return metars, tafs, meta, issue_tokens


def write_json(path: Path, data: Any) -> int:
    text = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
    path.write_text(text, encoding="utf-8")
    return len(text.encode("utf-8"))


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", default="site/data", type=Path, help="output directory")
    parser.add_argument(
        "--from-dir",
        type=Path,
        metavar="DIR",
        help="read the three *.cache.*.gz files from DIR instead of downloading",
    )
    args = parser.parse_args(argv)

    try:
        caches = load_caches(args.from_dir)
        metars, tafs, meta, issue_tokens = build(caches)
    except (BuildError, ET.ParseError, ValueError) as exc:
        print(f"build_site_data: {exc}", file=sys.stderr)
        return 1
    if not metars:
        print("build_site_data: no usable METARs, refusing to write an empty map", file=sys.stderr)
        return 1

    args.out.mkdir(parents=True, exist_ok=True)
    sizes = {
        "metars.json": write_json(args.out / "metars.json", metars),
        "tafs.json": write_json(args.out / "tafs.json", tafs),
        "meta.json": write_json(args.out / "meta.json", meta),
    }

    print(f"METARs: {meta['metar_count']}  TAFs: {meta['taf_count']}")
    print(f"clean rate: {meta['clean_rate']:.2%}")
    print(
        f"category agreement: {meta['category_agreement']:.2%} "
        f"of {meta['category_compared']} compared"
    )
    if issue_tokens:
        top = ", ".join(f"{tok or '(empty)'} x{n}" for tok, n in issue_tokens.most_common(10))
        print(f"top issue tokens: {top}")
    for name, size in sizes.items():
        print(f"{args.out / name}: {size / 1024:.1f} KiB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
