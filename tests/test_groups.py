import pytest

from metar_parse.groups import (
    parse_cloud,
    parse_day_time,
    parse_period,
    parse_pressure,
    parse_rvr,
    parse_temperature,
    parse_visibility,
    parse_weather,
    parse_wind,
    parse_wind_variation,
)
from metar_parse.models import DayTime


def test_day_time():
    assert parse_day_time("121630Z") == DayTime(12, 16, 30)
    assert parse_day_time("321630Z") is None
    assert parse_day_time("1216Z") is None


def test_period():
    assert parse_period("1218/1324") == (DayTime(12, 18), DayTime(13, 24))
    assert parse_period("9999") is None


@pytest.mark.parametrize(
    "token, direction, speed, gust, unit, variable",
    [
        ("24012G22KT", 240, 12, 22, "KT", False),
        ("VRB03KT", None, 3, None, "KT", True),
        ("00000KT", 0, 0, None, "KT", False),
        ("27015MPS", 270, 15, None, "MPS", False),
        ("360105G125KT", 360, 105, 125, "KT", False),
        ("/////KT", None, None, None, "KT", False),
    ],
)
def test_wind(token, direction, speed, gust, unit, variable):
    w = parse_wind(token)
    assert w is not None
    assert (w.direction_deg, w.speed, w.gust, w.unit, w.variable) == (
        direction,
        speed,
        gust,
        unit,
        variable,
    )


def test_wind_conversions():
    w = parse_wind("27010MPS")
    assert w.speed_kt == pytest.approx(19.4, abs=0.1)
    assert parse_wind("00000KT").calm


def test_wind_rejects_non_wind():
    assert parse_wind("9999") is None
    assert parse_wind("40010KT") is None


def test_wind_variation():
    assert parse_wind_variation("200V270") == (200, 270)
    assert parse_wind_variation("200V") is None


@pytest.mark.parametrize(
    "tokens, meters, consumed",
    [
        (["9999"], 9999, 1),
        (["0800NE"], 800, 1),
        (["10SM"], 16093.4, 1),
        (["1/4SM"], 402.3, 1),
        (["1", "1/2SM"], 2414.0, 2),
        (["P6SM"], 9656.1, 1),
    ],
)
def test_visibility(tokens, meters, consumed):
    vis, n = parse_visibility(tokens, 0)
    assert n == consumed
    assert vis.meters == pytest.approx(meters, abs=0.1)


def test_visibility_modifiers_and_direction():
    assert parse_visibility(["M1/4SM"], 0)[0].modifier == "M"
    assert parse_visibility(["1500SW"], 0)[0].direction == "SW"
    assert parse_visibility(["////"], 0)[0].value is None
    assert parse_visibility(["BKN035"], 0) == (None, 0)


def test_rvr():
    r = parse_rvr("R24/0600U")
    assert (r.runway, r.value, r.unit, r.tendency) == ("24", 600, "M", "U")
    r = parse_rvr("R10L/1000V1800FT")
    assert (r.runway, r.value, r.variable_high, r.unit) == ("10L", 1000, 1800, "FT")
    assert parse_rvr("R27/P1500N").modifier == "P"


@pytest.mark.parametrize(
    "token, intensity, descriptor, phenomena, recent",
    [
        ("-SHRA", "-", "SH", ["RA"], False),
        ("+TSRAGR", "+", "TS", ["RA", "GR"], False),
        ("VCFG", "VC", None, ["FG"], False),
        ("FZDZ", "", "FZ", ["DZ"], False),
        ("TS", "", "TS", [], False),
        ("RERA", "", None, ["RA"], True),
        ("BR", "", None, ["BR"], False),
    ],
)
def test_weather(token, intensity, descriptor, phenomena, recent):
    wx = parse_weather(token)
    assert wx is not None
    assert (wx.intensity, wx.descriptor, wx.phenomena, wx.recent) == (
        intensity,
        descriptor,
        phenomena,
        recent,
    )


def test_weather_rejects_non_weather():
    for token in ("BKN035", "Q1012", "NOSIG", "AUTO", "RMK", "FZ"):
        assert parse_weather(token) is None


def test_cloud():
    c = parse_cloud("FEW020CB")
    assert (c.cover, c.height_ft, c.cloud_type) == ("FEW", 2000, "CB")
    assert parse_cloud("BKN035").is_ceiling
    assert not parse_cloud("SCT035").is_ceiling
    assert parse_cloud("VV002").height_ft == 200
    assert parse_cloud("NSC").cover == "NSC"
    assert parse_cloud("BKN///").height_ft is None
    assert parse_cloud("9999") is None


def test_temperature():
    assert parse_temperature("14/09") == (14.0, 9.0)
    assert parse_temperature("M02/M05") == (-2.0, -5.0)
    assert parse_temperature("M01/") == (-1.0, None)
    assert parse_temperature("1/4SM") is None


def test_pressure():
    q = parse_pressure("Q1012")
    assert (q.hpa, q.inhg) == (1012.0, 29.88)
    a = parse_pressure("A2992")
    assert (a.value, a.unit, a.hpa) == (29.92, "inHg", 1013.2)
