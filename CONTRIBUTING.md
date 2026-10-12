# Contributing

Thanks for helping make MetarParse better!

## Reporting a bug

Open an issue with the **exact raw report** (METAR or TAF) and what you expected
to get. Reports from real stations are ideal; they often become test cases.

## Making a change

1. Fork and clone the repo, then install the dev tools:
   ```bash
   pip install -e ".[dev]"
   ```
2. Add a focused test in `tests/` that reproduces the problem.
3. Make the fix. Parsers must never raise on bad input: record a `ParseIssue` instead.
4. Check everything passes:
   ```bash
   pytest -q
   ruff check . && ruff format --check .
   ```
5. Add a line under **Unreleased** in `CHANGELOG.md` and open a pull request against `main`.

## Guidelines

- Standard library only; Python 3.9+ compatible.
- Keep group decoders in `groups.py` small and side-effect free.
- Only regenerate `tests/fixtures/` (`python scripts/fetch_fixtures.py`) when you
  need new real-world coverage, and keep request rates low.

By contributing you agree that your work is released under the MIT license.
