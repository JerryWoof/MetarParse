#!/usr/bin/env python3
"""Download a real-world METAR/TAF corpus for the regression tests.

Writes ``tests/fixtures/metars.txt`` and ``tests/fixtures/tafs.txt`` with one
report per line (multi-line TAFs are joined into one line), deduplicated and
sorted, so the corpus tests can check that the parsers never choke on real data.

Sources (both free, no API key):

* aviationweather.gov Data API - the last ``--hours`` of METARs and the
  current TAFs for a diverse station list, plus TAFs for a few regional
  bounding boxes.
* Iowa Environmental Mesonet ASOS archive - METARs from a handful of
  historical events with rare weather (tornadoes, blizzards, hurricanes,
  haboobs, volcanic ash, wildfire smoke, ice storms, dense fog).

The script makes roughly 20 requests in total and pauses between them.
Please keep it that way; both services are run for the public good.

Usage::

    python scripts/fetch_fixtures.py            # default: 24 h of METARs
    python scripts/fetch_fixtures.py --hours 12 --no-history
"""

from __future__ import annotations

import argparse
import csv
import datetime
import io
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Sequence, Set, Tuple

USER_AGENT = "metar-parse-fixtures/0.1 (+https://github.com/JerryWoof/MetarParse)"
AWC_BASE = "https://aviationweather.gov/api/data"
IEM_ASOS = "https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py"
PAUSE_S = 1.5
CHUNK = 25

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures"

# Diverse station list. Comments say why a station is interesting.
STATIONS: Dict[str, List[str]] = {
    "us": [
        "KJFK", "KLGA", "KBOS", "KORD", "KATL", "KDFW", "KDEN", "KLAX", "KSFO", "KSEA",
        "KMIA", "KMSY", "KPHX", "KLAS", "KSLC", "KMSP", "KDTW", "KIAD", "KOKC", "KTUL",
        "KICT", "KTPA", "KMCO", "KBTV", "KCYS",
        "KASE", "KEGE", "KLXV",  # mountain airports
        "KMWN",  # Mount Washington summit, extreme winds
        "KHSV", "KJAN", "KBTR",  # thunderstorm-prone south
        "KAQO", "KBAZ", "KRYW",  # small AUTO stations (AO1/AO2, sensor flags)
        "KNKX", "KNZY", "KDMA", "KNFW",  # military
        "PANC", "PAFA", "PABR", "PAJN", "PADQ",  # Alaska incl. Utqiagvik
        "PHNL", "PHOG", "PHTO",  # Hawaii
        "PGUM", "TJSJ",  # Guam, Puerto Rico
    ],
    "canada": ["CYYZ", "CYUL", "CYVR", "CYYC", "CYWG", "CYFB", "CYRB", "CYXY"],
    "europe": [
        "LKPR", "LKTB", "EGLL", "EGPH", "EIDW", "EDDF", "EDDM", "LFPG", "LOWW", "LSZH",
        "EPWA", "ENGM", "ENSB", "ESSA", "EFHK", "BIKF", "EHAM", "EBBR", "LEMD", "LPPT",
        "LIRF", "LGAV", "LTFM", "LHBP", "UKBB", "UUEE", "UUWW", "ULLI",
    ],
    "asia_middle_east": [
        "ZBAA", "ZSPD", "RJTT", "RJCC", "RKSI", "VHHH", "RCTP", "WSSS", "VTBS", "VIDP",
        "VABB", "OMDB", "OERK", "OTHH", "LLBG", "UAAA", "UNNT", "UHMM",
    ],
    "southern_hemisphere": [
        "YSSY", "YMML", "YPPH", "NZAA", "NZCH", "SAEZ", "SCEL", "SBGR", "SKBO", "SPJC",
        "FAOR", "FACT", "FIMP", "HKJK", "DNMM", "NFFN",
    ],
    "polar_odd": [
        "BGTL", "BGSF", "NZSP", "NZCM", "SCRM", "EGYP",  # Greenland, Antarctica, Falklands
        "MMMX", "SEQM", "SLLP",  # high altitude (Mexico City, Quito, La Paz)
        "FHAW", "NSTU", "GCLP", "LPAZ", "TFFR",  # remote islands
    ],
}  # fmt: skip

# Extra TAFs by bounding box (minLat, minLon, maxLat, maxLon) for volume/variety.
TAF_BBOXES: List[Tuple[str, str]] = [
    ("central_europe", "45,5,55,20"),
    ("us_plains", "30,-105,42,-90"),
    ("east_asia", "20,100,45,145"),
    ("australia", "-45,110,-10,155"),
]

# Historical events with rare weather, fetched from the IEM ASOS archive.
# (label, stations, first day, last day)
HISTORY: List[Tuple[str, Sequence[str], str, str]] = [
    ("Moore tornado", ["OKC", "OUN", "PWA"], "2013-05-20", "2013-05-20"),
    ("Blizzard Juno", ["BOS", "ORH"], "2015-01-27", "2015-01-27"),
    ("Hurricane Sandy", ["JFK", "ACY"], "2012-10-29", "2012-10-29"),
    ("Hurricane Ian", ["RSW", "FMY"], "2022-09-28", "2022-09-28"),
    ("Phoenix haboob", ["PHX", "CHD"], "2011-07-05", "2011-07-06"),
    ("Redoubt volcanic ash", ["PANC", "PAEN"], "2009-03-28", "2009-03-28"),
    ("Pacific NW wildfire smoke", ["SEA", "PDX"], "2020-09-12", "2020-09-12"),
    ("Texas ice storm", ["DFW", "AUS"], "2021-02-15", "2021-02-15"),
    ("London dense fog", ["EGLL", "EGKK"], "2006-12-21", "2006-12-21"),
    ("Moscow freezing rain", ["UUEE", "UUDD"], "2010-12-26", "2010-12-26"),
]


def log(msg: str) -> None:
    print(msg, file=sys.stderr)


def http_get(url: str, params: Iterable[Tuple[str, str]]) -> str:
    full = f"{url}?{urllib.parse.urlencode(list(params))}"
    req = urllib.request.Request(full, headers={"User-Agent": USER_AGENT})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                body = resp.read().decode("utf-8", errors="replace")
            time.sleep(PAUSE_S)
            return body
        except (urllib.error.URLError, TimeoutError) as exc:
            log(f"  request failed ({exc}); retry {attempt + 1}/3")
            time.sleep(PAUSE_S * (attempt + 2))
    log(f"  giving up on {full}")
    return ""


def chunks(items: Sequence[str], size: int) -> Iterator[Sequence[str]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


def normalize(line: str) -> str:
    return " ".join(line.split())


def split_tafs(text: str) -> List[str]:
    """Join the indented continuation lines of the raw TAF feed."""
    reports: List[str] = []
    current: List[str] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        if line.startswith("TAF") and current:
            reports.append(normalize(" ".join(current)))
            current = []
        current.append(line)
    if current:
        reports.append(normalize(" ".join(current)))
    return [r for r in reports if r.startswith("TAF")]


def fetch_recent_metars(stations: Sequence[str], hours: int) -> List[str]:
    out: List[str] = []
    for group in chunks(stations, CHUNK):
        log(f"METAR {group[0]}..{group[-1]} ({len(group)} stations)")
        body = http_get(
            f"{AWC_BASE}/metar",
            [("ids", ",".join(group)), ("format", "raw"), ("hours", str(hours))],
        )
        out.extend(normalize(line) for line in body.splitlines() if line.strip())
    return out


def fetch_tafs(stations: Sequence[str]) -> List[str]:
    out: List[str] = []
    for group in chunks(stations, CHUNK):
        log(f"TAF {group[0]}..{group[-1]} ({len(group)} stations)")
        body = http_get(f"{AWC_BASE}/taf", [("ids", ",".join(group)), ("format", "raw")])
        out.extend(split_tafs(body))
    for label, bbox in TAF_BBOXES:
        log(f"TAF bbox {label}")
        body = http_get(f"{AWC_BASE}/taf", [("bbox", bbox), ("format", "raw")])
        out.extend(split_tafs(body))
    return out


def fetch_history() -> List[str]:
    out: List[str] = []
    for label, stations, first, last in HISTORY:
        log(f"IEM {label}: {', '.join(stations)} {first}..{last}")
        y1, m1, d1 = first.split("-")
        # IEM end date is exclusive, so ask for the day after `last`.
        end = datetime.date.fromisoformat(last) + datetime.timedelta(days=1)
        y2, m2, d2 = end.isoformat().split("-")
        params = [("station", s) for s in stations] + [
            ("data", "metar"),
            ("year1", y1), ("month1", m1), ("day1", d1),
            ("year2", y2), ("month2", m2), ("day2", d2),
            ("tz", "Etc/UTC"), ("format", "onlycomma"), ("latlon", "no"),
            ("missing", "empty"), ("trace", "empty"),
            ("report_type", "3"), ("report_type", "4"),
        ]  # fmt: skip
        body = http_get(IEM_ASOS, params)
        for row in csv.DictReader(io.StringIO(body)):
            metar = normalize(row.get("metar") or "")
            if not metar:
                continue
            # IEM strips the report type; restore it so lines look like the live feed.
            if not metar.startswith(("METAR ", "SPECI ")):
                metar = f"METAR {metar}"
            out.append(metar)
    return out


def dedupe_sort(lines: Iterable[str]) -> List[str]:
    seen: Set[str] = set()
    for line in lines:
        if line:
            seen.add(line)

    def key(line: str) -> Tuple[str, str]:
        parts = line.split()
        # Sort by station, then by the rest of the line (day/time comes first).
        station = next((p for p in parts[1:] if len(p) == 4 and p.isalnum()), "")
        return station, line

    return sorted(seen, key=key)


def write(path: Path, lines: List[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    log(f"wrote {len(lines)} reports to {path.relative_to(ROOT)} ({path.stat().st_size} bytes)")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--hours", type=int, default=24, help="hours of recent METARs")
    parser.add_argument("--no-history", action="store_true", help="skip the IEM archive")
    parser.add_argument("--out", type=Path, default=FIXTURES, help="output directory")
    args = parser.parse_args(argv)

    stations = sorted({s for group in STATIONS.values() for s in group})
    log(f"{len(stations)} stations")

    metars = fetch_recent_metars(stations, args.hours)
    found = {line.split()[1] for line in metars if len(line.split()) > 1}
    missing = [s for s in stations if s not in found]
    if missing:
        log(f"no recent METARs for: {', '.join(missing)}")
    if not args.no_history:
        metars.extend(fetch_history())
    tafs = fetch_tafs(stations)

    metars, tafs = dedupe_sort(metars), dedupe_sort(tafs)
    if not metars or not tafs:
        log("nothing downloaded; leaving fixtures untouched")
        return 1
    write(args.out / "metars.txt", metars)
    write(args.out / "tafs.txt", tafs)
    return 0


if __name__ == "__main__":
    sys.exit(main())
