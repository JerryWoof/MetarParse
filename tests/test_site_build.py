"""Offline tests for ``scripts/build_site_data.py`` using tiny hand-written caches."""

import gzip
import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "build_site_data.py"


@pytest.fixture(scope="module")
def builder():
    spec = importlib.util.spec_from_file_location("build_site_data", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


HEADER = (
    "raw_text,station_id,observation_time,latitude,longitude,temp_c,"
    "sky_cover,cloud_base_ft_agl,sky_cover,cloud_base_ft_agl,flight_category,metar_type,elevation_m"
)

METAR_ROWS = [
    # older LKPR report, must be replaced by the newer one below
    '"METAR LKPR 121400Z 27005KT 9999 FEW030 10/04 Q1023",LKPR,'
    "2026-10-12T14:00:00.000Z,50.1,14.26,10,FEW,3000,,,VFR,METAR,364",
    '"METAR LKPR 121430Z 28008KT 250V320 9999 -SHRA FEW023 11/05 Q1022 NOSIG",LKPR,'
    "2026-10-12T14:30:00.000Z,50.1,14.26,11,FEW,2300,,,VFR,METAR,364",
    # US report; AWC says IFR but we decode OVC007 -> IFR as well
    '"SPECI KJFK 121451Z 35010KT 2SM BR OVC007 18/17 A2981 RMK AO2",KJFK,'
    "2026-10-12T14:51:00.000Z,40.64,-73.76,18,OVC,700,,,IFR,SPECI,3",
    # AWC disagrees (says VFR, we decode LIFR)
    '"METAR EGLL 121450Z 17011KT 0400 FG VV001 12/12 Q1013",EGLL,'
    "2026-10-12T14:50:00.000Z,51.48,-0.46,12,,,,,VFR,METAR,26",
    # month rollover: report from the 30th, observation time 30 Sep
    '"METAR EDDF 302350Z 22005KT CAVOK 08/03 Q1020",EDDF,'
    "2026-09-30T23:50:00.000Z,50.03,8.57,8,,,,,,METAR,111",
    # no coordinates anywhere -> skipped
    '"METAR XXXX 121450Z 00000KT 9999 SKC 20/10 Q1015",XXXX,'
    "2026-10-12T14:50:00.000Z,-99.99,-99.99,20,,,,,VFR,METAR,9999",
    # bad rows: empty raw text, short row, garbage time
    ",ZZZZ,2026-10-12T14:50:00.000Z,10,10,,,,,,VFR,METAR,1",
    '"METAR SHRT 121450Z"',
    '"garbage that is not a metar",GRBG,not-a-time,10,20,,,,,,,,',
]

TAF_XML = """<?xml version="1.0" encoding="UTF-8"?>
<response><data num_results="3">
<TAF><raw_text><![CDATA[TAF LKPR 121100Z 1212/1312 24012KT 9999 FEW030
  TEMPO 1214/1218 4000 SHRA BECMG 1300/1302 BKN008 PROB30 TEMPO 1306/1310 -RA]]></raw_text>
<station_id>LKPR</station_id><issue_time>2026-10-12T11:00:00.000Z</issue_time></TAF>
<TAF><raw_text><![CDATA[TAF LKPR 120500Z 1206/1306 24012KT 9999 FEW030]]></raw_text>
<station_id>LKPR</station_id><issue_time>2026-10-12T05:00:00.000Z</issue_time></TAF>
<TAF><raw_text><![CDATA[TAF KJFK 121130Z 1212/1318 35012KT P6SM SCT020
  FM122000 31009KT 2SM BR OVC004 FM131200 VRB03KT P6SM SKC]]></raw_text>
<station_id>KJFK</station_id><issue_time>2026-10-12T11:30:00.000Z</issue_time></TAF>
<TAF><raw_text><![CDATA[TAF NOPE 121100Z 1212/1312 24012KT 9999 FEW030]]></raw_text>
<station_id>NOPE</station_id><issue_time>2026-10-12T11:00:00.000Z</issue_time></TAF>
</data></response>
"""

STATIONS = [
    {"id": "LKPR", "icaoId": "LKPR", "iataId": "PRG", "site": "Prague/Havel Arpt",
     "lat": 50.101, "lon": 14.26, "elev": 364, "country": "CZ"},
    {"id": "KJFK", "icaoId": "KJFK", "iataId": "JFK", "site": "New York/JF Kennedy Intl",
     "lat": 40.63916, "lon": -73.76394, "elev": 3, "country": "US"},
    {"id": "32012", "icaoId": None, "iataId": None, "site": "Buoy", "lat": 19.7,
     "lon": -85.5, "elev": 0, "country": None},
]  # fmt: skip

METAR_KEYS = {
    "id", "iata", "name", "country", "lat", "lon", "elev_m", "time", "type", "cat",
    "temp", "dew", "rh", "wdir", "wspd", "gust", "wvar", "vrb", "vis_m", "ceil_ft",
    "cavok", "clouds", "wx", "wx_text", "qnh", "raw", "issues", "taf",
}  # fmt: skip
GROUP_KEYS = {
    "kind", "prob", "from", "to", "cat", "wdir", "wspd", "gust", "vrb", "vis_m",
    "ceil_ft", "cavok", "clouds", "wx", "wx_text", "raw",
}  # fmt: skip
META_KEYS = {
    "generated", "library_version", "metar_count", "taf_count", "clean_rate",
    "category_agreement", "category_compared", "source", "wheel",
}  # fmt: skip


def write_caches(directory, metar_rows=METAR_ROWS, taf_xml=TAF_XML, stations=STATIONS):
    directory.mkdir(parents=True, exist_ok=True)
    csv_text = "\n".join([HEADER, *metar_rows]) + "\n"
    (directory / "metars.cache.csv.gz").write_bytes(gzip.compress(csv_text.encode()))
    (directory / "tafs.cache.xml.gz").write_bytes(gzip.compress(taf_xml.encode()))
    (directory / "stations.cache.json.gz").write_bytes(gzip.compress(json.dumps(stations).encode()))
    return directory


@pytest.fixture
def built(builder, tmp_path, capsys):
    src = write_caches(tmp_path / "cache")
    out = tmp_path / "out"
    assert builder.main(["--from-dir", str(src), "--out", str(out)]) == 0
    summary = capsys.readouterr().out
    metars = json.loads((out / "metars.json").read_text())
    tafs = json.loads((out / "tafs.json").read_text())
    meta = json.loads((out / "meta.json").read_text())
    return {m["id"]: m for m in metars}, tafs, meta, summary, out


def test_schema_keys(built):
    metars, tafs, meta, _, _ = built
    assert set(meta) == META_KEYS
    for entry in metars.values():
        assert set(entry) == METAR_KEYS
    for taf in tafs.values():
        assert set(taf) == {"raw", "issued", "from", "to", "groups"}
        for group in taf["groups"]:
            assert set(group) == GROUP_KEYS


def test_compact_json(built):
    out = built[4]
    text = (out / "metars.json").read_text()
    assert '": ' not in text
    assert "}, {" not in text


def test_latest_per_station_and_fields(built):
    metars = built[0]
    prg = metars["LKPR"]
    assert prg["raw"].startswith("METAR LKPR 121430Z")
    assert prg["time"] == "2026-10-12T14:30:00Z"
    assert prg["iata"] == "PRG"
    assert prg["name"] == "Prague/Havel Arpt"
    assert prg["country"] == "CZ"
    assert (prg["lat"], prg["lon"], prg["elev_m"]) == (50.101, 14.26, 364)
    assert prg["wdir"] == 280 and prg["wspd"] == 8 and prg["gust"] is None
    assert prg["wvar"] == [250, 320]
    assert prg["vis_m"] == 9999.0
    assert prg["clouds"] == [["FEW", 2300, None]]
    assert prg["wx"] == ["-SHRA"]
    assert prg["wx_text"] == "light showers of rain"
    assert prg["qnh"] == 1022.0
    assert prg["cat"] == "VFR"
    assert prg["taf"] is True
    assert prg["issues"] == []

    jfk = metars["KJFK"]
    assert jfk["type"] == "SPECI"
    assert jfk["cat"] == "IFR"
    assert jfk["ceil_ft"] == 700
    assert jfk["qnh"] == 1009.5  # A2981
    assert jfk["vis_m"] == 3218.7
    assert jfk["lat"] == 40.639  # rounded station coordinates


def test_month_rollover_and_awc_fallbacks(built):
    eddf = built[0]["EDDF"]
    assert eddf["time"] == "2026-09-30T23:50:00Z"
    assert eddf["cavok"] is True and eddf["vis_m"] == 9999.0
    assert eddf["iata"] is None and eddf["name"] is None  # not in stations cache
    assert (eddf["lat"], eddf["lon"]) == (50.03, 8.57)  # AWC CSV coordinates
    assert eddf["taf"] is False


def test_bad_rows_and_missing_coordinates_skipped(built):
    metars = built[0]
    assert "XXXX" not in metars  # -99.99 placeholder coordinates
    assert "ZZZZ" not in metars  # empty raw text
    assert "SHRT" not in metars  # no station id column
    assert set(metars) == {"LKPR", "KJFK", "EGLL", "EDDF", "GRBG"}
    assert metars["GRBG"]["issues"]  # decoded, issues reported, not crashed


def test_category_agreement(built):
    meta = built[2]
    # compared: LKPR (VFR=VFR), KJFK (IFR=IFR), EGLL (VFR!=LIFR), GRBG (no AWC cat) -> 2/3
    assert meta["category_compared"] == 3
    assert meta["category_agreement"] == round(2 / 3, 4)
    assert meta["metar_count"] == 5
    assert meta["taf_count"] == 2
    assert meta["clean_rate"] == 0.8  # GRBG has issues
    assert meta["wheel"] == f"pkg/metar_parse-{meta['library_version']}-py3-none-any.whl"
    assert meta["source"] == "aviationweather.gov"
    assert meta["generated"].endswith("Z")


def test_taf_groups(built):
    tafs = built[1]
    assert "NOPE" not in tafs  # no METAR station on the map
    prg = tafs["LKPR"]
    assert prg["issued"] == "2026-10-12T11:00:00Z"  # newest of the two LKPR TAFs
    assert prg["from"] == "2026-10-12T12:00:00Z"
    assert prg["to"] == "2026-10-13T12:00:00Z"
    base, tempo, becmg, prob = prg["groups"]
    assert base["kind"] == "BASE" and base["cat"] == "VFR"
    assert (base["from"], base["to"]) == ("2026-10-12T12:00:00Z", "2026-10-13T12:00:00Z")
    assert base["wdir"] == 240 and base["wspd"] == 12
    assert tempo["kind"] == "TEMPO"
    assert (tempo["from"], tempo["to"]) == ("2026-10-12T14:00:00Z", "2026-10-12T18:00:00Z")
    assert tempo["cat"] == "IFR"  # 4000 m < 3 SM
    assert tempo["wx"] == ["SHRA"]
    assert becmg["kind"] == "BECMG" and becmg["cat"] == "IFR" and becmg["ceil_ft"] == 800
    assert becmg["from"] == "2026-10-13T00:00:00Z"
    assert prob["prob"] == 30
    assert prob["cat"] is None  # only weather, no visibility or clouds
    assert prob["wx_text"] == "light rain"

    jfk = tafs["KJFK"]
    kinds = [g["kind"] for g in jfk["groups"]]
    assert kinds == ["BASE", "FM", "FM"]
    fm1, fm2 = jfk["groups"][1:]
    assert fm1["from"] == "2026-10-12T20:00:00Z"
    assert fm1["to"] == "2026-10-13T12:00:00Z"
    assert fm1["cat"] == "LIFR"
    assert fm2["vrb"] is True and fm2["wdir"] is None
    assert fm2["to"] == "2026-10-13T18:00:00Z"


def test_summary_printed(built):
    summary = built[3]
    assert "METARs: 5" in summary
    assert "TAFs: 2" in summary
    assert "metars.json" in summary


def test_missing_cache_file_fails(builder, tmp_path, capsys):
    src = write_caches(tmp_path / "cache")
    (src / "tafs.cache.xml.gz").unlink()
    assert builder.main(["--from-dir", str(src), "--out", str(tmp_path / "out")]) == 1
    assert not (tmp_path / "out").exists()


def test_no_metars_fails(builder, tmp_path, capsys):
    src = write_caches(tmp_path / "cache", metar_rows=[])
    assert builder.main(["--from-dir", str(src), "--out", str(tmp_path / "out")]) == 1
    assert "no usable METARs" in capsys.readouterr().err


def test_dangling_hour_24_in_taf(builder):
    taf = builder.encode_taf(
        "TAF EGLL 301700Z 3018/0124 24012KT 9999 FEW030 TEMPO 3020/3024 4000 SHRA",
        builder.parse_time("2026-09-30T17:00:00.000Z"),
    )
    assert taf["from"] == "2026-09-30T18:00:00Z"
    assert taf["to"] == "2026-10-02T00:00:00Z"
    assert taf["groups"][1]["to"] == "2026-10-01T00:00:00Z"
