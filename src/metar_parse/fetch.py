"""Minimal client for the free aviationweather.gov Data API (no API key needed).

Only the standard library is used. Network access happens solely in
:func:`fetch_metars` and :func:`fetch_tafs`.
"""

from __future__ import annotations

import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Iterable, List, Optional

from . import __version__

BASE_URL = "https://aviationweather.gov/api/data"
USER_AGENT = f"metar-parse/{__version__} (+https://github.com/JerryWoof/MetarParse)"

_STATION_RE = re.compile(r"^[A-Z0-9]{3,4}$")


class FetchError(Exception):
    """Raised when reports cannot be downloaded (HTTP or network failure)."""


def _normalize_stations(stations: Iterable[str]) -> List[str]:
    ids = [s.strip().upper() for s in stations]
    if not ids:
        raise ValueError("at least one station ID is required")
    bad = [s for s in ids if not _STATION_RE.match(s)]
    if bad:
        raise ValueError(f"invalid station ID(s): {', '.join(repr(s) for s in bad)}")
    return ids


def _get(endpoint: str, params: dict, timeout: float) -> str:
    url = f"{BASE_URL}/{endpoint}?{urllib.parse.urlencode(params, safe=',')}"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
    except urllib.error.HTTPError as exc:
        raise FetchError(f"HTTP {exc.code} from {url}: {exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise FetchError(f"cannot reach aviationweather.gov: {exc.reason}") from exc
    except OSError as exc:  # timeouts, connection resets
        raise FetchError(f"network error while fetching {url}: {exc}") from exc
    return body.decode("utf-8", errors="replace")


def fetch_metars(
    stations: Iterable[str], hours: Optional[float] = None, timeout: float = 10
) -> List[str]:
    """Download raw METAR/SPECI reports, one string per report.

    ``hours`` asks for that many hours of history instead of only the latest
    observation.
    """
    params = {"ids": ",".join(_normalize_stations(stations)), "format": "raw"}
    if hours is not None:
        if hours <= 0:
            raise ValueError("hours must be positive")
        params["hours"] = f"{hours:g}"
    text = _get("metar", params, timeout)
    return [line.strip() for line in text.splitlines() if line.strip()]


def split_tafs(text: str) -> List[str]:
    """Split raw multi-line TAF text into single-line reports.

    A report starts at a line beginning with ``TAF``; indented continuation
    lines (``FM...``, ``TEMPO ...``) are joined onto it with single spaces.
    """
    reports: List[List[str]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("TAF") or not reports:
            reports.append([stripped])
        else:
            reports[-1].append(stripped)
    return [" ".join(parts) for parts in reports]


def fetch_tafs(stations: Iterable[str], timeout: float = 10) -> List[str]:
    """Download raw TAFs, one single-line string per report."""
    params = {"ids": ",".join(_normalize_stations(stations)), "format": "raw"}
    return split_tafs(_get("taf", params, timeout))
