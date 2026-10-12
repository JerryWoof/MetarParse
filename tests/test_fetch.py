import io
import socket
import urllib.error
import urllib.request

import pytest

from metar_parse import __version__, fetch
from metar_parse.fetch import FetchError, fetch_metars, fetch_tafs, split_tafs


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


@pytest.fixture
def urlopen(monkeypatch):
    """Replace urlopen; returns a dict that records the request and sets the body."""
    state = {"body": b"", "requests": []}

    def fake(request, timeout=None):
        state["requests"].append((request, timeout))
        if "error" in state:
            raise state["error"]
        return FakeResponse(state["body"])

    monkeypatch.setattr(urllib.request, "urlopen", fake)
    return state


def test_fetch_metars(urlopen):
    urlopen["body"] = (
        b"METAR LKPR 121630Z 24012KT 9999 FEW020 14/09 Q1012 NOSIG\n"
        b"\n"
        b"METAR EGLL 121620Z 27010KT CAVOK 15/08 Q1015\n"
    )
    reports = fetch_metars(["lkpr", "EGLL"])
    assert reports == [
        "METAR LKPR 121630Z 24012KT 9999 FEW020 14/09 Q1012 NOSIG",
        "METAR EGLL 121620Z 27010KT CAVOK 15/08 Q1015",
    ]
    request, timeout = urlopen["requests"][0]
    assert request.full_url == (
        "https://aviationweather.gov/api/data/metar?ids=LKPR,EGLL&format=raw"
    )
    assert request.get_header("User-agent") == (
        f"metar-parse/{__version__} (+https://github.com/JerryWoof/MetarParse)"
    )
    assert timeout == 10


def test_fetch_metars_hours(urlopen):
    fetch_metars(["KJFK"], hours=3, timeout=5)
    request, timeout = urlopen["requests"][0]
    assert request.full_url.endswith("ids=KJFK&format=raw&hours=3")
    assert timeout == 5
    fetch_metars(["KJFK"], hours=1.5)
    assert urlopen["requests"][1][0].full_url.endswith("&hours=1.5")


def test_empty_response(urlopen):
    assert fetch_metars(["LKPR"]) == []
    assert fetch_tafs(["LKPR"]) == []


@pytest.mark.parametrize("stations", [[], ["LK PR"], ["TOOLONG"], ["AB"], ["LKPR&x=1"]])
def test_invalid_stations(urlopen, stations):
    with pytest.raises(ValueError):
        fetch_metars(stations)
    assert urlopen["requests"] == []


def test_invalid_hours(urlopen):
    with pytest.raises(ValueError):
        fetch_metars(["LKPR"], hours=0)


def test_fetch_tafs_splits_reports(urlopen):
    urlopen["body"] = (
        b"TAF KJFK 121130Z 1212/1318 24012KT P6SM FEW050\n"
        b"  FM121800 27015G25KT P6SM SCT040\n"
        b"  FM130000 30010KT P6SM SKC\n"
        b"TAF AMD EGLL 121105Z 1212/1318 25010KT 9999 SCT030\n"
        b"  TEMPO 1214/1218 7000 -SHRA\n"
    )
    assert fetch_tafs(["KJFK", "EGLL"]) == [
        "TAF KJFK 121130Z 1212/1318 24012KT P6SM FEW050 "
        "FM121800 27015G25KT P6SM SCT040 FM130000 30010KT P6SM SKC",
        "TAF AMD EGLL 121105Z 1212/1318 25010KT 9999 SCT030 TEMPO 1214/1218 7000 -SHRA",
    ]
    assert urlopen["requests"][0][0].full_url == (
        "https://aviationweather.gov/api/data/taf?ids=KJFK,EGLL&format=raw"
    )


def test_split_tafs_without_taf_prefix():
    assert split_tafs("\nKJFK 121130Z 1212/1318 24012KT\n  FM121800 27015KT\n") == [
        "KJFK 121130Z 1212/1318 24012KT FM121800 27015KT"
    ]


@pytest.mark.parametrize(
    "error, match",
    [
        (urllib.error.HTTPError("u", 503, "Service Unavailable", {}, None), "HTTP 503"),
        (urllib.error.URLError("Name or service not known"), "cannot reach"),
        (socket.timeout("timed out"), "network error"),
    ],
    ids=["http-error", "url-error", "timeout"],  # HTTPError has no safe repr on 3.9
)
def test_network_errors(urlopen, error, match):
    urlopen["error"] = error
    with pytest.raises(FetchError, match=match):
        fetch.fetch_metars(["LKPR"])
