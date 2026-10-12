# Site data format

`scripts/build_site_data.py` downloads the aviationweather.gov bulk caches,
decodes every report with MetarParse and writes static JSON for the map in
`site/data/`. The files are generated in CI and never committed.

All times are ISO 8601 UTC strings (`"2026-10-12T14:30:00Z"`). Missing values
are `null`. Units: temperatures °C, wind kt, visibility metres, heights ft,
pressure hPa.

## `data/meta.json`

```json
{
  "generated": "2026-10-12T15:10:00Z",
  "library_version": "0.1.0",
  "metar_count": 5012,
  "taf_count": 3020,
  "clean_rate": 0.9974,           // share of METARs decoded with no issues
  "category_agreement": 0.991,    // share where our flight category == AWC's
  "category_compared": 4800,      // how many reports had an AWC category to compare
  "source": "aviationweather.gov",
  "wheel": "pkg/metar_parse-0.1.0-py3-none-any.whl"
}
```

## `data/metars.json`

A JSON array, one object per station (latest report only):

```json
{
  "id": "LKPR",                   // ICAO
  "iata": "PRG",                  // or null
  "name": "Prague/Havel Arpt",
  "country": "CZ",                // or null
  "lat": 50.101, "lon": 14.26, "elev_m": 364,
  "time": "2026-10-12T14:30:00Z",
  "type": "METAR",                // or SPECI
  "cat": "VFR",                   // VFR | MVFR | IFR | LIFR | null
  "temp": 11.0, "dew": 5.0, "rh": 66.5,
  "wdir": 280,                    // null when variable or missing
  "wspd": 8, "gust": null,        // knots
  "wvar": [250, 320],             // or null
  "vrb": false,
  "vis_m": 9999.0, "ceil_ft": null,
  "cavok": false,
  "clouds": [["FEW", 2300, null]],          // [cover, height_ft, CB/TCU/null]
  "wx": ["-SHRA"],                          // raw present-weather codes
  "wx_text": "light showers of rain",       // human readable, or ""
  "qnh": 1022.0,                            // hPa, or null
  "raw": "METAR LKPR 121430Z 28008KT 250V320 9999 FEW023 11/05 Q1022 NOSIG",
  "issues": [],                             // ["TOKEN: message", ...]
  "taf": true                               // a TAF exists in tafs.json
}
```

## `data/tafs.json`

An object keyed by ICAO:

```json
{
  "LKPR": {
    "raw": "TAF LKPR 121100Z 1212/1312 ...",
    "issued": "2026-10-12T11:00:00Z",
    "from": "2026-10-12T12:00:00Z",
    "to": "2026-10-13T12:00:00Z",
    "groups": [
      {
        "kind": "BASE",           // BASE | FM | BECMG | TEMPO | INTER | PROB
        "prob": null,             // 30 / 40 or null
        "from": "2026-10-12T12:00:00Z",
        "to": "2026-10-13T12:00:00Z",
        "cat": "VFR",             // flight category of this group, or null if it
                                  // does not state enough (e.g. TEMPO with only wx)
        "wdir": 240, "wspd": 12, "gust": null, "vrb": false,
        "vis_m": 9999.0, "ceil_ft": null, "cavok": false,
        "clouds": [["FEW", 3000, null]],
        "wx": [], "wx_text": "",
        "raw": "24012KT 9999 FEW030"
      }
    ]
  }
}
```

## `pkg/`

The MetarParse wheel built from the current commit
(`pip wheel . --no-deps -w site/pkg`), used by the in-browser decoder
(`decode.html`, Pyodide). Its path is in `meta.json` → `wheel`.
