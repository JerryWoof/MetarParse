# MetarParse

[![CI](https://github.com/JerryWoof/MetarParse/actions/workflows/ci.yml/badge.svg)](https://github.com/JerryWoof/MetarParse/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.9%20%7C%203.10%20%7C%203.11%20%7C%203.12%20%7C%203.13-blue)](https://github.com/JerryWoof/MetarParse)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

**Fast, tolerant METAR & TAF parser for Python.**

**[🌍 MetarParse Live](https://jerrywoof.github.io/MetarParse/)**: live weather for
~5,000 airports worldwide, decoded by this library every 30 minutes.
**[Decode a METAR in your browser →](https://jerrywoof.github.io/MetarParse/decode.html)**

Decodes raw aviation weather reports into typed dataclasses and clean JSON. No
dependencies, no exceptions on bad input: tokens it cannot decode end up in
`report.issues` instead.

```pycon
>>> from metar_parse import parse_metar
>>> m = parse_metar("METAR LKPR 121630Z 24012G22KT 9999 -SHRA FEW020CB BKN035 14/09 Q1012 BECMG 4000")
>>> m.station
'LKPR'
>>> m.wind.speed_kt, m.wind.gust_kt
(12.0, 22.0)
>>> m.visibility.meters  # 9999 = 10 km or more
9999.0
>>> m.clouds
[CloudLayer(cover='FEW', height_ft=2000, cloud_type='CB'), CloudLayer(cover='BKN', height_ft=3500, cloud_type=None)]
>>> m.ceiling_ft
3500
>>> m.temperature_c, m.dewpoint_c
(14.0, 9.0)
>>> m.pressure.hpa
1012.0
>>> m.flight_category
'VFR'
>>> m.trends[0].kind, m.trends[0].visibility.meters
('BECMG', 4000.0)
>>> m.issues  # tokens that could not be decoded
[]
>>> m.to_dict()["wind"]  # plain dicts, ready for json.dumps
{'direction_deg': 240, 'speed': 12, 'gust': 22, 'unit': 'KT', 'variable': False, 'variable_from_deg': None, 'variable_to_deg': None}
```

Not sure whether a string is a METAR or a TAF? `metar_parse.parse(text)` picks
the right parser for you.

## Features

- METAR and SPECI, ICAO and US (FAA) formats: KT / MPS / KMH winds, metres and
  statute-mile visibility (`1 1/2SM`, `P6SM`), RVR, present and recent weather,
  cloud layers incl. CB/TCU and vertical visibility, CAVOK, `Q`/`A` pressure
- Trend groups (`NOSIG`, `BECMG`, `TEMPO` with `FM`/`TL`/`AT`), wind shear, runway state
- US remarks: `AO2`, `SLP`, precise temperature, precipitation, pressure tendency,
  peak wind, wind shift, sensor status and more
- TAFs: base forecast, `FM`, `BECMG`, `TEMPO`, `PROB30/40`, `TX`/`TN`, `AMD`/`COR`/`NIL`/`CNL`
- Derived values: flight category (VFR / MVFR / IFR / LIFR), ceiling, relative humidity
- Unit helpers on every model (`speed_kt`, `meters`, `statute_miles`, `hpa`, `inhg`)
- Tolerant by design, regression-tested against thousands of real reports from
  130+ stations worldwide
- Command-line tool that fetches live reports
- Pure standard library, typed, Python 3.9+

## Install

```bash
pip install metar-parse
```

From source:

```bash
git clone https://github.com/JerryWoof/MetarParse.git
cd MetarParse
pip install -e ".[dev]"
```

## Command line

```bash
metar LKPR EGLL                 # latest METARs, decoded
metar --taf KJFK                # latest TAF
metar --json LKPR               # JSON output
metar --raw "METAR LKPR 121630Z 24012G22KT 9999 -SHRA FEW020CB BKN035 14/09 Q1012"
```

```
LKPR  METAR day 12 16:30Z  [VFR]
  Wind        240° 12 kt, gusts 22 kt
  Visibility  10 km or more
  Weather     light showers of rain
  Clouds      few 2,000 ft CB, broken 3,500 ft  (ceiling 3,500 ft)
  Temp        14 °C / dew point 9 °C  (RH 71.8 %)
  Pressure    1012 hPa (29.88 inHg)
```

Run `metar --help` for all options.

## How a METAR works

`METAR LKPR 121630Z 24012G22KT 9999 -SHRA FEW020CB BKN035 14/09 Q1012 BECMG 4000`

| Token        | Meaning                                                     | Field                      |
|--------------|-------------------------------------------------------------|----------------------------|
| `METAR`      | Routine observation (`SPECI` = special)                     | `report_type`              |
| `LKPR`       | ICAO station: Prague Václav Havel Airport                   | `station`                  |
| `121630Z`    | Day 12, 16:30 UTC                                           | `time`                     |
| `24012G22KT` | Wind from 240°, 12 kt, gusting 22 kt                        | `wind`                     |
| `9999`       | Visibility 10 km or more                                    | `visibility`               |
| `-SHRA`      | Light (`-`) showers (`SH`) of rain (`RA`)                   | `weather`                  |
| `FEW020CB`   | Few clouds (1-2 oktas) at 2,000 ft, cumulonimbus            | `clouds`                   |
| `BKN035`     | Broken (5-7 oktas) at 3,500 ft: the ceiling                 | `clouds`, `ceiling_ft`     |
| `14/09`      | Temperature 14 °C, dew point 9 °C (`M` prefix = minus)      | `temperature_c`, `dewpoint_c` |
| `Q1012`      | QNH 1012 hPa (US: `A2992` = 29.92 inHg)                     | `pressure`                 |
| `BECMG 4000` | Trend: visibility becoming 4,000 m within the next 2 hours  | `trends`                   |

## TAF

```pycon
>>> from metar_parse import parse_taf
>>> t = parse_taf(
...     "TAF LKPR 121400Z 1215/1321 30004KT CAVOK TEMPO 1215/1218 SCT037 "
...     "PROB30 TEMPO 1303/1308 VRB02KT 5000 BR MIFG NSC BECMG 1316/1318 22010KT"
... )
>>> t.station, str(t.valid_from), str(t.valid_to)
('LKPR', '121500Z', '132100Z')
>>> [g.kind for g in t.groups]
['BASE', 'TEMPO', 'TEMPO', 'BECMG']
>>> t.groups[2].probability, t.groups[2].visibility.meters  # PROB30 TEMPO
(30, 5000.0)
```

## Data source

Live reports come from the [aviationweather.gov Data API](https://aviationweather.gov/data/api/),
run by the NOAA Aviation Weather Center. It is free and needs no key, but it is
a shared public service: batch stations into one request, cache results, and do
not poll more often than reports change (METARs are typically issued every
30-60 minutes). MetarParse sends an identifying `User-Agent`.

Decoded data is for information only and is **not** an official source for flight planning.

## Development

```bash
uv venv && uv pip install -e ".[dev]"   # or: python -m venv .venv && pip install -e ".[dev]"
pytest -q
ruff check . && ruff format --check .
```

`tests/test_corpus.py` parses a real-world corpus in `tests/fixtures/` and fails if
any report raises or if the share of reports with `issues` exceeds a threshold.
To refresh the corpus (about 20 polite requests to aviationweather.gov and the
[Iowa Environmental Mesonet](https://mesonet.agron.iastate.edu/) archive):

```bash
python scripts/fetch_fixtures.py
```

## Contributing

Bug reports with the raw report that broke the parser are the most valuable
contribution. See [CONTRIBUTING.md](CONTRIBUTING.md) and [CHANGELOG.md](CHANGELOG.md).

## License

[MIT](LICENSE) © Vojtech Kraus
