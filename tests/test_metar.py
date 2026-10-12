import json

import pytest

from metar_parse.metar import parse_metar
from metar_parse.models import DayTime


def test_european_full():
    m = parse_metar(
        "METAR LKPR 121630Z 24012G22KT 9999 -SHRA FEW020CB BKN035 14/09 Q1012 BECMG 4000"
    )
    assert m.report_type == "METAR"
    assert m.station == "LKPR"
    assert m.time == DayTime(12, 16, 30)
    assert (m.wind.direction_deg, m.wind.speed, m.wind.gust, m.wind.unit) == (240, 12, 22, "KT")
    assert m.visibility.meters == 9999
    assert [w.raw for w in m.weather] == ["-SHRA"]
    assert [(c.cover, c.height_ft, c.cloud_type) for c in m.clouds] == [
        ("FEW", 2000, "CB"),
        ("BKN", 3500, None),
    ]
    assert (m.temperature_c, m.dewpoint_c) == (14.0, 9.0)
    assert m.pressure.hpa == 1012
    assert len(m.trends) == 1
    assert m.trends[0].kind == "BECMG"
    assert m.trends[0].visibility.meters == 4000
    assert m.trends[0].raw == "BECMG 4000"
    assert m.issues == []


def test_us_with_remarks():
    m = parse_metar(
        "KJFK 121651Z 31015G25KT 10SM FEW050 SCT250 18/06 A2992 RMK AO2 SLP132 T01830061"
    )
    assert m.report_type == "METAR"
    assert m.station == "KJFK"
    assert m.visibility.value == 10 and m.visibility.unit == "SM"
    assert m.pressure.unit == "inHg" and m.pressure.value == 29.92
    assert m.remarks is not None
    assert m.remarks.raw == "AO2 SLP132 T01830061"
    assert m.issues == []
    json.dumps(m.to_dict())


def test_speci():
    m = parse_metar("SPECI KORD 121712Z 27020G35KT 2SM +TSRA BKN015CB 20/18 A2985")
    assert m.report_type == "SPECI"
    assert m.weather[0].intensity == "+"
    assert m.weather[0].descriptor == "TS"
    assert m.ceiling_ft == 1500
    assert m.issues == []


@pytest.mark.parametrize(
    "raw, auto, corrected",
    [
        ("METAR LKPR 121630Z AUTO 24005KT 9999 NCD 14/09 Q1012", True, False),
        ("METAR COR LKPR 121630Z 24005KT 9999 NCD 14/09 Q1012", False, True),
        ("METAR LKPR 121630Z COR 24005KT 9999 NCD 14/09 Q1012", False, True),
        ("METAR LKPR 121630Z COR AUTO 24005KT 9999 NCD 14/09 Q1012", True, True),
    ],
)
def test_auto_cor(raw, auto, corrected):
    m = parse_metar(raw)
    assert (m.auto, m.corrected) == (auto, corrected)
    assert m.station == "LKPR"
    assert m.clouds[0].cover == "NCD"
    assert m.issues == []


@pytest.mark.parametrize("raw", ["METAR LKPR 121630Z NIL=", "LKPR 121630Z NIL"])
def test_nil(raw):
    m = parse_metar(raw)
    assert m.nil
    assert m.station == "LKPR"
    assert m.wind is None
    assert m.issues == []


def test_cavok_mps_variable_wind_runway_state():
    m = parse_metar("METAR UUEE 121630Z 27005MPS 240V300 CAVOK 15/08 Q1015 R24/290050 NOSIG=")
    assert m.wind.unit == "MPS"
    assert m.wind.speed_kt == pytest.approx(9.7)
    assert (m.wind.variable_from_deg, m.wind.variable_to_deg) == (240, 300)
    assert m.cavok
    assert m.runway_state == ["R24/290050"]
    assert [t.kind for t in m.trends] == ["NOSIG"]
    assert m.issues == []


def test_china_mps():
    m = parse_metar("ZBAA 121600Z 36004MPS 330V030 9999 FEW040 23/12 Q1009 NOSIG")
    assert m.station == "ZBAA"
    assert m.wind.speed == 4 and m.wind.unit == "MPS"
    assert m.wind.variable_from_deg == 330
    assert m.trends[0].kind == "NOSIG"
    assert m.issues == []


def test_vrb_wind():
    m = parse_metar("EGLL 121620Z VRB02KT 9999 SCT030 12/08 Q1020")
    assert m.wind.variable
    assert m.wind.direction_deg is None


@pytest.mark.parametrize(
    "vis_tokens, value, modifier",
    [
        ("1 1/2SM", 1.5, None),
        ("M1/4SM", 0.25, "M"),
        ("P6SM", 6.0, "P"),
        ("3/4SM", 0.75, None),
        ("2 3/4SM", 2.75, None),
    ],
)
def test_us_visibility(vis_tokens, value, modifier):
    m = parse_metar(f"KBOS 121654Z 05008KT {vis_tokens} BR OVC004 10/09 A3001")
    assert m.visibility.unit == "SM"
    assert m.visibility.value == value
    assert m.visibility.modifier == modifier
    assert m.issues == []


def test_directional_min_visibility():
    m = parse_metar("LKPR 121630Z 24005KT 4000 1500SW BR BKN005 08/07 Q1012")
    assert m.visibility.meters == 4000
    assert m.min_visibility.meters == 1500
    assert m.min_visibility.direction == "SW"
    assert m.issues == []


def test_rvr():
    m = parse_metar(
        "EDDF 121620Z 25004KT 0350 R25L/0450U R25C/P1500N R07R/0300V0600D FG VV001 05/05 Q1018"
    )
    assert [r.runway for r in m.runway_visual_range] == ["25L", "25C", "07R"]
    assert m.runway_visual_range[0].tendency == "U"
    assert m.runway_visual_range[1].modifier == "P"
    assert m.runway_visual_range[2].variable_high == 600
    assert m.clouds[0].cover == "VV" and m.clouds[0].height_ft == 100
    assert m.ceiling_ft == 100
    assert m.issues == []


@pytest.mark.parametrize("cover", ["NSC", "NCD", "SKC", "CLR"])
def test_clear_sky(cover):
    m = parse_metar(f"LKPR 121630Z 24005KT 9999 {cover} 14/09 Q1012")
    assert [c.cover for c in m.clouds] == [cover]
    assert m.ceiling_ft is None


@pytest.mark.parametrize(
    "token, temp, dew",
    [("M02/M05", -2.0, -5.0), ("00/M01", 0.0, -1.0), ("M10/", -10.0, None), ("05/05", 5.0, 5.0)],
)
def test_temperatures(token, temp, dew):
    m = parse_metar(f"ULLI 121630Z 36003MPS 9999 OVC010 {token} Q1030")
    assert (m.temperature_c, m.dewpoint_c) == (temp, dew)
    assert m.issues == []


def test_recent_weather_and_windshear():
    m = parse_metar(
        "LKPR 121630Z 24012KT 9999 -RA BKN020 14/12 Q1008 RETSRA RERA WS R24 WS RWY06 WS ALL RWY"
    )
    assert [w.raw for w in m.weather] == ["-RA"]
    assert [w.raw for w in m.recent_weather] == ["RETSRA", "RERA"]
    assert all(w.recent for w in m.recent_weather)
    assert m.windshear == ["WS R24", "WS RWY06", "WS ALL RWY"]
    assert m.issues == []


@pytest.mark.parametrize("state", ["R24/290050", "R88/CLRD70", "88290050", "R24L/CLRD//"])
def test_runway_state(state):
    m = parse_metar(f"UUEE 121630Z 27005MPS 9999 SCT030 15/08 Q1015 {state}")
    assert m.runway_state == [state]
    assert m.issues == []


def test_tempo_fm_tl():
    m = parse_metar(
        "EGLL 121620Z 22015KT 9999 SCT030 15/10 Q1005 "
        "TEMPO FM1700 TL1800 4000 SHRA BKN014CB BECMG AT1900 30020G30KT NSW"
    )
    assert [t.kind for t in m.trends] == ["TEMPO", "BECMG"]
    tempo, becmg = m.trends
    assert tempo.from_time == DayTime(None, 17, 0)
    assert tempo.until_time == DayTime(None, 18, 0)
    assert tempo.visibility.meters == 4000
    assert tempo.weather[0].raw == "SHRA"
    assert tempo.clouds[0].cloud_type == "CB"
    assert tempo.raw == "TEMPO FM1700 TL1800 4000 SHRA BKN014CB"
    assert becmg.at_time == DayTime(None, 19, 0)
    assert becmg.wind.gust == 30
    assert becmg.nsw
    assert m.issues == []


def test_trend_cavok_then_remarks():
    m = parse_metar("LKPR 121630Z 24005KT 6000 BKN010 10/09 Q1012 BECMG CAVOK RMK REF")
    assert m.trends[0].cavok
    assert m.remarks.raw == "REF"


def test_pressure_both_units_keeps_first():
    m = parse_metar("MMMX 121640Z 05010KT 7SM SCT080 22/05 A3030 Q1026")
    assert m.pressure.unit == "inHg"
    assert m.issues == []


def test_missing_groups_are_quiet():
    m = parse_metar("METAR LKPR 121630Z AUTO /////KT //// // ////// //////CB M01/// Q////")
    assert m.auto
    assert m.wind.speed is None
    assert m.visibility is None
    assert m.issues == []


def test_trailing_equals_and_whitespace():
    m = parse_metar("  METAR   LKPR 121630Z\n24005KT 9999 FEW030 14/09 Q1012=  ")
    assert m.raw == "METAR LKPR 121630Z 24005KT 9999 FEW030 14/09 Q1012"
    assert m.pressure.hpa == 1012
    assert m.issues == []


def test_garbage_tokens_produce_issues():
    m = parse_metar("METAR LKPR 121630Z 24005KT XYZZY 9999 FOO123 14/09 Q1012 @@ 999V100")
    assert m.wind.speed == 5
    assert m.pressure.hpa == 1012
    assert [i.token for i in m.issues] == ["XYZZY", "FOO123", "@@", "999V100"]


def test_garbage_in_trend():
    m = parse_metar("LKPR 121630Z 24005KT 9999 14/09 Q1012 TEMPO BLAH 3000")
    assert m.trends[0].visibility.meters == 3000
    assert [i.token for i in m.issues] == ["BLAH"]


@pytest.mark.parametrize("raw", ["", "   ", "=", "METAR", "123", "R/ / /", "WS", "1"])
def test_degenerate_input_never_raises(raw):
    m = parse_metar(raw)
    assert m.station is None
    assert m.issues


def test_wind_variation_without_wind():
    m = parse_metar("LKPR 121630Z 200V270 9999")
    assert m.wind is None
    assert m.issues[0].token == "200V270"
