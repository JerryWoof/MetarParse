# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.0] - 2026-10-12

### Added

- `parse_metar` for METAR/SPECI reports in ICAO and US formats, including trend
  groups, RVR, wind shear and runway state.
- `parse_remarks` for common US/ASOS `RMK` groups.
- `parse_taf` for TAFs with `FM`, `BECMG`, `TEMPO`, `INTER`, `PROB30/40` and `TX`/`TN` groups.
- `parse()` that picks the METAR or TAF parser from the report text.
- Derived values: flight category, ceiling and relative humidity.
- Typed dataclass models with unit helpers and `to_dict()` for JSON output.
- Live fetching from the aviationweather.gov Data API.
- `metar` command-line tool (`--taf`, `--json`, `--raw`).
- Real-world regression corpus and `scripts/fetch_fixtures.py` to regenerate it.

[Unreleased]: https://github.com/JerryWoof/MetarParse/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/JerryWoof/MetarParse/releases/tag/v0.1.0
