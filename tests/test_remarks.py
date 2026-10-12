from __future__ import annotations

import pytest

from metar_parse.models import DayTime, PeakWind, PressureTendency
from metar_parse.remarks import parse_remarks


def test_full_us_remark():
    text = (
        "AO2 PK WND 28045/1955 WSHFT 1940 FROPA TSB05E30 SLP132 P0012 60034 "
        "T01830061 10194 20172 401940172 58033 $"
    )
    r = parse_remarks(text)
    assert r.raw == text
    assert r.station_type == "AO2"
    assert r.peak_wind == PeakWind(280, 45, DayTime(None, 19, 55))
    assert r.wind_shift == DayTime(None, 19, 40)
    assert r.wind_shift_frontal is True
    assert r.sea_level_pressure_hpa == pytest.approx(1013.2)
    assert r.hourly_precip_in == pytest.approx(0.12)
    assert r.precip_3_6h_in == pytest.approx(0.34)
    assert r.precise_temperature_c == pytest.approx(18.3)
    assert r.precise_dewpoint_c == pytest.approx(6.1)
    assert r.max_temp_6h_c == pytest.approx(19.4)
    assert r.min_temp_6h_c == pytest.approx(17.2)
    assert r.max_temp_24h_c == pytest.approx(19.4)
    assert r.min_temp_24h_c == pytest.approx(17.2)
    assert r.pressure_tendency == PressureTendency(code=8, change_hpa=3.3)
    assert r.maintenance_needed is True
    assert r.unparsed == ["TSB05E30"]


@pytest.mark.parametrize("text", ["", "   ", None])
def test_empty(text):
    r = parse_remarks(text)
    assert r.station_type is None
    assert r.unparsed == []
    assert r.maintenance_needed is False


@pytest.mark.parametrize("token", ["AO1", "AO2"])
def test_station_type(token):
    assert parse_remarks(token).station_type == token


@pytest.mark.parametrize(
    "token, expected",
    [("SLP132", 1013.2), ("SLP982", 998.2), ("SLP500", 950.0), ("SLP000", 1000.0)],
)
def test_sea_level_pressure(token, expected):
    assert parse_remarks(token).sea_level_pressure_hpa == pytest.approx(expected)


def test_slpno_is_sensor_status():
    r = parse_remarks("AO2 SLPNO")
    assert r.sea_level_pressure_hpa is None
    assert r.sensor_status == ["SLPNO"]


def test_precise_temperature_negative():
    r = parse_remarks("T10171025")
    assert r.precise_temperature_c == pytest.approx(-1.7)
    assert r.precise_dewpoint_c == pytest.approx(-2.5)


def test_precise_temperature_without_dewpoint():
    r = parse_remarks("T0123")
    assert r.precise_temperature_c == pytest.approx(12.3)
    assert r.precise_dewpoint_c is None


def test_precipitation_groups():
    r = parse_remarks("P0000 60125 70250")
    assert r.hourly_precip_in == 0.0
    assert r.precip_3_6h_in == pytest.approx(1.25)
    assert r.precip_24h_in == pytest.approx(2.5)


def test_indeterminate_precipitation():
    r = parse_remarks("P//// 6//// 7////")
    assert r.hourly_precip_in is None
    assert r.precip_3_6h_in is None
    assert r.precip_24h_in is None
    assert r.unparsed == []


def test_negative_extremes():
    r = parse_remarks("11021 21105 410211105")
    assert r.max_temp_6h_c == pytest.approx(-2.1)
    assert r.min_temp_6h_c == pytest.approx(-10.5)
    assert r.max_temp_24h_c == pytest.approx(-2.1)
    assert r.min_temp_24h_c == pytest.approx(-10.5)


def test_pressure_tendency_falling():
    assert parse_remarks("56012").pressure_tendency == PressureTendency(6, 1.2)


def test_snow_depth_and_sunshine():
    r = parse_remarks("4/021 98096")
    assert r.snow_depth_in == 21
    assert r.sunshine_minutes == 96


def test_peak_wind_three_digit_speed():
    r = parse_remarks("PK WND 090105/0012")
    assert r.peak_wind == PeakWind(90, 105, DayTime(None, 0, 12))


def test_peak_wind_minutes_only_uses_hour_minus_one():
    r = parse_remarks("PK WND 28045/55")
    assert r.peak_wind == PeakWind(280, 45, DayTime(None, -1, 55))


def test_bad_peak_wind_is_unparsed():
    r = parse_remarks("PK WND 99945/1955")
    assert r.peak_wind is None
    assert r.unparsed == ["PK", "WND", "99945/1955"]


def test_wind_shift_without_fropa():
    r = parse_remarks("WSHFT 30")
    assert r.wind_shift == DayTime(None, -1, 30)
    assert r.wind_shift_frontal is False


def test_wind_shift_missing_time():
    r = parse_remarks("WSHFT")
    assert r.wind_shift is None
    assert r.unparsed == ["WSHFT"]


def test_pressure_flags():
    r = parse_remarks("PRESRR")
    assert r.pressure_rising_rapidly is True
    assert r.pressure_falling_rapidly is False
    assert parse_remarks("PRESFR").pressure_falling_rapidly is True


def test_sensor_status_with_locations():
    r = parse_remarks("RVRNO PWINO PNO FZRANO TSNO VISNO RWY06 CHINO RWY24L")
    assert r.sensor_status == [
        "RVRNO",
        "PWINO",
        "PNO",
        "FZRANO",
        "TSNO",
        "VISNO RWY06",
        "CHINO RWY24L",
    ]
    assert r.unparsed == []


@pytest.mark.parametrize(
    "text, expected",
    [
        ("LTG DSNT NE", ["LTG DSNT NE"]),
        ("OCNL LTGICCG OHD", ["OCNL LTGICCG OHD"]),
        ("FRQ LTGCGIC VC S-SW", ["FRQ LTGCGIC VC S-SW"]),
        ("CONS LTGIC ALQDS", ["CONS LTGIC ALQDS"]),
        ("LTG DSNT N AND SE", ["LTG DSNT N AND SE"]),
    ],
)
def test_lightning(text, expected):
    r = parse_remarks(text)
    assert r.lightning == expected
    assert r.unparsed == []


def test_lightning_followed_by_other_groups():
    r = parse_remarks("AO2 OCNL LTGICCG DSNT SW TSB05 SLP101")
    assert r.lightning == ["OCNL LTGICCG DSNT SW"]
    assert r.unparsed == ["TSB05"]
    assert r.sea_level_pressure_hpa == pytest.approx(1010.1)


def test_frequency_word_alone_is_unparsed():
    assert parse_remarks("OCNL RA").unparsed == ["OCNL", "RA"]


def test_maintenance_glued_to_last_group():
    r = parse_remarks("AO2 SLP132$")
    assert r.maintenance_needed is True
    assert r.sea_level_pressure_hpa == pytest.approx(1013.2)


def test_other_remarks_kept_in_order():
    r = parse_remarks("AO2 RAB15E30 CIG 005V010 VIS 1/2V2 TWR VIS 1 1/2 ACFT MSHP")
    assert r.station_type == "AO2"
    assert r.unparsed == [
        "RAB15E30",
        "CIG",
        "005V010",
        "VIS",
        "1/2V2",
        "TWR",
        "VIS",
        "1",
        "1/2",
        "ACFT",
        "MSHP",
    ]


def test_european_remarks():
    r = parse_remarks("QFE754 WIND THR21 25010KT")
    assert r.unparsed == ["QFE754", "WIND", "THR21", "25010KT"]


@pytest.mark.parametrize(
    "text",
    [
        "!!@@## /// $$$ PK",
        "PK WND",
        "T1 T9999999 SLP 4/ 5 98 6 P",
        "WSHFT 9999 FROPA",
        "VISNO",
        "LTG",
        "AND OCNL FRQ",
        "\t\n$",
        "AO3 SLP12 T012 59999",
    ],
)
def test_garbage_does_not_raise(text):
    r = parse_remarks(text)
    assert r.raw == text


def test_garbage_details():
    r = parse_remarks("WSHFT 9999 FROPA 59999 LTG")
    assert r.wind_shift is None
    assert r.pressure_tendency is None
    assert r.unparsed == ["WSHFT", "9999", "FROPA", "59999"]
    assert r.lightning == ["LTG"]
