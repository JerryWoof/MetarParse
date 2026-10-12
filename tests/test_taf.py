import json

import pytest

from metar_parse.models import DayTime, TafTemperature
from metar_parse.taf import parse_taf

EUROPEAN = (
    "TAF LKPR 121100Z 1212/1312 24012KT 9999 FEW030 "
    "TEMPO 1214/1218 4000 SHRA BKN020CB "
    "BECMG 1300/1302 VRB03KT "
    "PROB30 1304/1308 0800 FG"
)

US = """TAF KJFK 121130Z 1212/1318 22012KT P6SM FEW050
     FM121800 24015G25KT P6SM SCT040 BKN250
     FM130200 27008KT 5SM -SHRA OVC015
     FM131200 30010KT P6SM SKC="""


def test_european_header():
    taf = parse_taf(EUROPEAN)
    assert taf.station == "LKPR"
    assert taf.issued == DayTime(12, 11, 0)
    assert taf.valid_from == DayTime(12, 12)
    assert taf.valid_to == DayTime(13, 12)
    assert taf.issues == []


def test_european_groups():
    taf = parse_taf(EUROPEAN)
    assert [g.kind for g in taf.groups] == ["BASE", "TEMPO", "BECMG", "PROB"]
    base, tempo, becmg, prob = taf.groups
    assert base.valid_from == taf.valid_from and base.valid_to == taf.valid_to
    assert base.wind.direction_deg == 240 and base.wind.speed == 12
    assert base.visibility.meters == 9999
    assert base.clouds[0].cover == "FEW" and base.clouds[0].height_ft == 3000
    assert base.raw == "24012KT 9999 FEW030"

    assert tempo.valid_from == DayTime(12, 14) and tempo.valid_to == DayTime(12, 18)
    assert tempo.visibility.meters == 4000
    assert tempo.weather[0].raw == "SHRA"
    assert tempo.clouds[0].cloud_type == "CB"
    assert tempo.raw == "TEMPO 1214/1218 4000 SHRA BKN020CB"

    assert becmg.wind.variable and becmg.wind.speed == 3

    assert prob.probability == 30
    assert prob.valid_from == DayTime(13, 4)
    assert prob.visibility.meters == 800
    assert prob.weather[0].phenomena == ["FG"]


def test_us_multiline_fm_groups():
    taf = parse_taf(US)
    assert taf.issues == []
    assert [g.kind for g in taf.groups] == ["BASE", "FM", "FM", "FM"]
    base, fm1, fm2, fm3 = taf.groups
    assert base.visibility.modifier == "P" and base.visibility.statute_miles == 6
    assert fm1.valid_from == DayTime(12, 18, 0)
    assert fm1.valid_to == DayTime(13, 2, 0)  # start of next FM group
    assert fm1.wind.gust == 25
    assert [c.cover for c in fm1.clouds] == ["SCT", "BKN"]
    assert fm2.valid_to == fm3.valid_from
    assert fm3.valid_to == taf.valid_to  # last FM runs to the TAF end
    assert fm3.clouds[0].cover == "SKC"
    assert fm2.raw == "FM130200 27008KT 5SM -SHRA OVC015"


def test_fm_not_ended_by_tempo():
    taf = parse_taf(
        "TAF KXYZ 121130Z 1212/1312 22012KT P6SM SKC "
        "FM121800 24010KT P6SM BKN040 TEMPO 1220/1222 3SM SHRA FM130000 VRB03KT P6SM SKC"
    )
    fm1 = taf.groups[1]
    assert fm1.valid_to == DayTime(13, 0, 0)


def test_without_taf_prefix_and_trailing_equals():
    taf = parse_taf("LKPR 121100Z 1212/1312 24012KT CAVOK=")
    assert taf.station == "LKPR"
    assert taf.groups[0].cavok
    assert taf.issues == []


@pytest.mark.parametrize(
    "raw, amended, corrected",
    [
        ("TAF AMD LKPR 121300Z 1213/1312 24012KT 9999 SCT030", True, False),
        ("TAF COR LKPR 121300Z 1213/1312 24012KT 9999 SCT030", False, True),
        ("AMD LKPR 121300Z 1213/1312 24012KT 9999 SCT030", True, False),
        ("TAF AMD COR LKPR 121300Z 1213/1312 24012KT 9999 SCT030", True, True),
    ],
)
def test_amd_cor(raw, amended, corrected):
    taf = parse_taf(raw)
    assert taf.amended is amended
    assert taf.corrected is corrected
    assert taf.station == "LKPR"
    assert taf.issues == []


def test_nil():
    taf = parse_taf("TAF EGLL 121100Z NIL=")
    assert taf.nil
    assert taf.station == "EGLL"
    assert taf.groups == []
    assert taf.issues == []


def test_cancelled():
    taf = parse_taf("TAF AMD LKPR 121300Z 1212/1312 CNL=")
    assert taf.cancelled and taf.amended
    assert taf.valid_to == DayTime(13, 12)
    assert taf.groups == []
    assert taf.issues == []


def test_cavok_and_nsw():
    taf = parse_taf("TAF LKPR 121100Z 1212/1312 24012KT 4000 RA BKN010 BECMG 1214/1216 CAVOK NSW")
    becmg = taf.groups[1]
    assert becmg.cavok and becmg.nsw
    assert becmg.weather == []
    assert taf.issues == []


def test_nsc_clouds():
    taf = parse_taf("TAF LKPR 121100Z 1212/1312 24012KT 9999 NSC")
    assert taf.groups[0].clouds[0].cover == "NSC"


def test_tx_tn_temperatures():
    taf = parse_taf("TAF LKPR 121100Z 1212/1312 24012KT 9999 FEW030 TX18/1214Z TNM02/1305Z")
    assert taf.temperatures == [
        TafTemperature("TX", 18, DayTime(12, 14)),
        TafTemperature("TN", -2, DayTime(13, 5)),
    ]
    assert taf.issues == []


def test_prob40_tempo():
    taf = parse_taf(
        "TAF LKPR 121100Z 1212/1312 24012KT 9999 FEW030 PROB40 TEMPO 1216/1220 TSRA BKN015CB"
    )
    group = taf.groups[1]
    assert group.kind == "TEMPO"
    assert group.probability == 40
    assert group.valid_from == DayTime(12, 16)
    assert group.weather[0].descriptor == "TS"
    assert group.raw == "PROB40 TEMPO 1216/1220 TSRA BKN015CB"
    assert taf.issues == []


def test_fractional_statute_miles():
    taf = parse_taf("TAF KBOS 121130Z 1212/1318 18010KT 1 1/2SM BR OVC004")
    base = taf.groups[0]
    assert base.visibility.value == 1.5 and base.visibility.unit == "SM"
    assert base.weather[0].phenomena == ["BR"]
    assert taf.issues == []


def test_remarks():
    taf = parse_taf("TAF KBOS 121130Z 1212/1318 18010KT P6SM SKC RMK NXT FCST BY 18Z")
    assert taf.remarks_raw == "NXT FCST BY 18Z"
    assert taf.issues == []


def test_old_style_periods():
    taf = parse_taf("TAF LKPR 121212 24012KT 9999 FEW030 TEMPO 1418 4000 SHRA FM1600 VRB02KT")
    assert taf.valid_from == DayTime(12, 12) and taf.valid_to == DayTime(13, 12)
    tempo, fm = taf.groups[1:]
    assert tempo.valid_from == DayTime(None, 14) and tempo.valid_to == DayTime(None, 18)
    assert fm.kind == "FM" and fm.valid_from == DayTime(None, 16, 0)
    assert taf.issues == []


def test_windshear_and_qnh_skipped_quietly():
    taf = parse_taf(
        "TAF KXYZ 121130Z 1212/1312 22012KT P6SM SKC WS020/24035KT QNH2992INS "
        "FM121800 24010KT P6SM BKN040"
    )
    assert taf.issues == []
    assert taf.groups[0].wind.speed == 12


def test_wind_variation_tolerated():
    taf = parse_taf("TAF LKPR 121100Z 1212/1312 24012KT 200V270 9999 FEW030")
    wind = taf.groups[0].wind
    assert (wind.variable_from_deg, wind.variable_to_deg) == (200, 270)
    assert taf.issues == []


def test_mps_wind():
    taf = parse_taf("TAF UUEE 121100Z 1212/1312 24005MPS 9999 BKN020")
    assert taf.groups[0].wind.unit == "MPS"


def test_garbage_tokens():
    taf = parse_taf("TAF LKPR 121100Z 1212/1312 24012KT XYZZY 9999 FEW030 @@@")
    assert [i.token for i in taf.issues] == ["XYZZY", "@@@"]
    assert taf.groups[0].visibility.meters == 9999


def test_change_group_without_period():
    taf = parse_taf("TAF LKPR 121100Z 1212/1312 24012KT 9999 TEMPO 4000 SHRA")
    tempo = taf.groups[1]
    assert tempo.kind == "TEMPO" and tempo.valid_from is None
    assert tempo.visibility.meters == 4000
    assert len(taf.issues) == 1


@pytest.mark.parametrize("raw", ["", "   ", "\n", "=", "TAF"])
def test_empty_inputs(raw):
    taf = parse_taf(raw)
    assert taf.raw == raw
    assert taf.groups == []


@pytest.mark.parametrize(
    "raw",
    [
        "TAF LKPR 991100Z 9999/9999 FM999999 PROB TEMPO BECMG",
        "TAF TEMPO PROB30 PROB40 TEMPO FM",
        "1 1/2SM 1",
        "TAF LKPR 121100Z 1212/1312 TX/Z TN99/9999Z WS/ RMK",
    ],
)
def test_never_raises(raw):
    taf = parse_taf(raw)
    json.dumps(taf.to_dict())


def test_raw_preserved_and_to_dict():
    taf = parse_taf(US)
    assert taf.raw == US
    data = taf.to_dict()
    assert data["groups"][1]["kind"] == "FM"
    json.dumps(data)
