import io
import json

import pytest

from metar_parse import __version__, cli
from metar_parse.fetch import FetchError
from metar_parse.models import (
    CloudLayer,
    DayTime,
    Metar,
    Pressure,
    Taf,
    TafChangeGroup,
    TafTemperature,
    Visibility,
    WeatherPhenomenon,
    Wind,
)

LKPR = "METAR LKPR 121630Z 24012G22KT 200V270 9999 -SHRA FEW020CB BKN035 14/09 Q1012"
TAF = "TAF LKPR 121100Z 1212/1312 24010KT 9999 SCT030 TEMPO 1214/1218 4000 SHRA"


def fake_metar(raw):
    return Metar(
        raw=raw,
        station=raw.split()[1],
        time=DayTime(12, 16, 30),
        wind=Wind(240, 12, gust=22, variable_from_deg=200, variable_to_deg=270),
        visibility=Visibility(9999),
        weather=[WeatherPhenomenon("-SHRA", "-", "SH", ["RA"])],
        clouds=[CloudLayer("FEW", 2000, "CB"), CloudLayer("BKN", 3500)],
        temperature_c=14.0,
        dewpoint_c=9.0,
        pressure=Pressure(1012.0),
    )


def fake_taf(raw):
    return Taf(
        raw=raw,
        station="LKPR",
        issued=DayTime(12, 11, 0),
        valid_from=DayTime(12, 12),
        valid_to=DayTime(13, 12),
        groups=[
            TafChangeGroup(
                wind=Wind(240, 10),
                visibility=Visibility(9999),
                clouds=[CloudLayer("SCT", 3000)],
            ),
            TafChangeGroup(
                kind="TEMPO",
                valid_from=DayTime(12, 14),
                valid_to=DayTime(12, 18),
                visibility=Visibility(4000),
                weather=[WeatherPhenomenon("SHRA", "", "SH", ["RA"])],
            ),
        ],
        temperatures=[TafTemperature("TX", 18, DayTime(12, 14))],
    )


@pytest.fixture
def env(monkeypatch):
    """Patch parsers and fetchers; record fetch calls. stdin defaults to a TTY."""
    calls = []
    monkeypatch.setattr(cli, "parse_metar", fake_metar)
    monkeypatch.setattr(cli, "parse_taf", fake_taf)

    def metars(stations, hours=None):
        calls.append(("metar", list(stations), hours))
        return [LKPR]

    def tafs(stations):
        calls.append(("taf", list(stations)))
        return [TAF]

    monkeypatch.setattr(cli, "fetch_metars", metars)
    monkeypatch.setattr(cli, "fetch_tafs", tafs)
    tty = io.StringIO()
    tty.isatty = lambda: True
    monkeypatch.setattr("sys.stdin", tty)
    return calls


def set_stdin(monkeypatch, text):
    stream = io.StringIO(text)
    stream.isatty = lambda: False
    monkeypatch.setattr("sys.stdin", stream)


def test_metar_summary(env, capsys):
    assert cli.main(["LKPR"]) == 0
    out = capsys.readouterr().out
    assert env == [("metar", ["LKPR"], None)]
    assert out.splitlines()[0] == "LKPR  METAR day 12 16:30Z  [VFR]"
    assert "240° 12 kt, gusts 22 kt (varying 200°–270°)" in out
    assert "10 km or more" in out
    assert "light showers of rain" in out
    assert "few 2,000 ft CB, broken 3,500 ft  (ceiling 3,500 ft)" in out
    assert "14 °C / dew point 9 °C  (RH 71.8 %)" in out
    assert "1012 hPa (29.88 inHg)" in out
    assert f"Raw         {LKPR}" in out


def test_hours_passed_through(env, capsys):
    assert cli.main(["--hours", "3", "lkpr", "EGLL"]) == 0
    assert env == [("metar", ["lkpr", "EGLL"], 3.0)]


def test_json_metar(env, capsys):
    assert cli.main(["--json", "LKPR"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert len(data) == 1
    assert data[0]["station"] == "LKPR"
    assert data[0]["flight_category"] == "VFR"
    assert data[0]["wind"]["gust"] == 22


def test_taf(env, capsys):
    assert cli.main(["--taf", "LKPR"]) == 0
    out = capsys.readouterr().out
    assert env == [("taf", ["LKPR"])]
    assert "LKPR  TAF issued day 12 11:00Z, valid day 12 12:00Z – day 13 12:00Z" in out
    assert "BASE" in out and "wind 240° 10 kt, vis 10 km or more, scattered 3,000 ft" in out
    assert "TEMPO       day 12 14:00Z – day 12 18:00Z: vis 4000 m, showers of rain" in out
    assert "max 18 °C at day 12 14:00Z" in out


def test_taf_json_has_no_flight_category(env, capsys):
    assert cli.main(["--taf", "--json", "LKPR"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data[0]["station"] == "LKPR" and "flight_category" not in data[0]


def test_raw_without_network(env, capsys):
    assert cli.main(["--raw", "METAR EGLL 121620Z 27010KT CAVOK 15/08 Q1015"]) == 0
    assert env == []
    assert capsys.readouterr().out.startswith("EGLL  METAR")


def test_raw_taf_autodetected(env, capsys):
    assert cli.main(["--raw", TAF, "--json"]) == 0
    assert "groups" in json.loads(capsys.readouterr().out)[0]


def test_stdin_dash(env, monkeypatch, capsys):
    set_stdin(monkeypatch, f"{LKPR}\n\nMETAR EGLL 121620Z 27010KT 9999 15/08 Q1015\n")
    assert cli.main(["-", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert [d["station"] for d in data] == ["LKPR", "EGLL"]
    assert env == []


def test_stdin_piped_without_args(env, monkeypatch, capsys):
    set_stdin(monkeypatch, LKPR + "\n")
    assert cli.main([]) == 0
    assert capsys.readouterr().out.startswith("LKPR  METAR")


def test_fetch_error(env, monkeypatch, capsys):
    def boom(stations, hours=None):
        raise FetchError("HTTP 503")

    monkeypatch.setattr(cli, "fetch_metars", boom)
    assert cli.main(["LKPR"]) == 1
    assert "HTTP 503" in capsys.readouterr().err


def test_no_reports(env, monkeypatch, capsys):
    monkeypatch.setattr(cli, "fetch_metars", lambda stations, hours=None: [])
    assert cli.main(["LKPR"]) == 1
    assert "no reports" in capsys.readouterr().err


def test_invalid_station_is_usage_error(env, monkeypatch, capsys):
    def invalid(stations, hours=None):
        raise ValueError("invalid station ID(s): 'X'")

    monkeypatch.setattr(cli, "fetch_metars", invalid)
    assert cli.main(["X"]) == 2
    assert "invalid station" in capsys.readouterr().err


@pytest.mark.parametrize(
    "argv",
    [
        [],  # nothing to do on a TTY
        ["LKPR", "-"],
        ["--raw", LKPR, "LKPR"],
        ["--taf", "--hours", "2", "LKPR"],
        ["--hours", "abc", "LKPR"],
        ["--bogus"],
    ],
)
def test_usage_errors(env, capsys, argv):
    assert cli.main(argv) == 2
    assert "usage:" in capsys.readouterr().err


def test_version(env, capsys):
    assert cli.main(["--version"]) == 0
    assert capsys.readouterr().out.strip() == f"metar {__version__}"


def test_formatters():
    assert cli.fmt_visibility(Visibility(1.5, "SM")) == "1 1/2 SM"
    assert cli.fmt_visibility(Visibility(0.25, "SM", modifier="M")) == "less than 1/4 SM"
    assert cli.fmt_visibility(Visibility(6, "SM", modifier="P")) == "more than 6 SM"
    assert cli.fmt_visibility(Visibility(800, direction="NE")) == "800 m to the NE"
    assert cli.fmt_visibility(Visibility(None)) == "not reported"
    assert cli.fmt_wind(Wind(0, 0)) == "calm"
    assert cli.fmt_wind(Wind(None, 3, variable=True)) == "variable 3 kt"
    assert cli.fmt_wind(Wind(270, 8, unit="MPS")) == "270° 8 m/s"
    assert cli.fmt_weather(WeatherPhenomenon("+TSRAGR", "+", "TS", ["RA", "GR"])) == (
        "heavy thunderstorm with rain and hail"
    )
    assert cli.fmt_weather(WeatherPhenomenon("VCTS", "VC", "TS", [])) == (
        "in the vicinity: thunderstorm"
    )
    assert cli.fmt_cloud(CloudLayer("VV", 200)) == "vertical visibility 200 ft"
    assert cli.fmt_cloud(CloudLayer("NSC")) == "no significant cloud"


def test_format_metar_cavok_and_inhg():
    m = Metar(
        raw="METAR KJFK ...",
        station="KJFK",
        cavok=True,
        pressure=Pressure(29.92, "inHg"),
        temperature_c=-2.0,
    )
    text = cli.format_metar(m)
    assert "[VFR]" in text
    assert "CAVOK" in text
    assert "29.92 inHg (1013.2 hPa)" in text
    assert "-2 °C" in text
