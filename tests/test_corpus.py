"""Bulk regression tests against a real-world corpus of METARs and TAFs.

The fixtures are produced by ``scripts/fetch_fixtures.py``. There is one test
per file (not one per report) to keep pytest output small; failures list the
first offending reports so they are easy to turn into focused unit tests.
"""

from __future__ import annotations

import traceback
from pathlib import Path
from typing import Callable, List, Tuple

import pytest

from metar_parse.metar import parse_metar
from metar_parse.taf import parse_taf

FIXTURES = Path(__file__).parent / "fixtures"

# Maximum share of reports that may carry at least one ParseIssue.
METAR_MAX_ISSUE_RATE = 0.01
TAF_MAX_ISSUE_RATE = 0.02

SHOW = 20


def _load(name: str) -> List[str]:
    path = FIXTURES / name
    if not path.exists():
        pytest.skip(f"{path} missing; run scripts/fetch_fixtures.py")
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _parse_all(lines: List[str], parse: Callable) -> Tuple[list, List[str]]:
    """Parse every line; return (parsed reports, crash descriptions)."""
    parsed, crashes = [], []
    for line in lines:
        try:
            parsed.append(parse(line))
        except Exception:
            crashes.append(f"{line}\n    {traceback.format_exc(limit=-1).strip()}")
    return parsed, crashes


@pytest.fixture(scope="module")
def metars() -> Tuple[list, List[str]]:
    return _parse_all(_load("metars.txt"), parse_metar)


@pytest.fixture(scope="module")
def tafs() -> Tuple[list, List[str]]:
    return _parse_all(_load("tafs.txt"), parse_taf)


def _report(title: str, items: List[str], total: int) -> str:
    head = f"{title}: {len(items)}/{total} reports"
    return "\n".join([head, *(f"  {item}" for item in items[:SHOW])])


def _issue_lines(reports: list) -> List[str]:
    return [
        f"{r.raw}\n    -> " + "; ".join(f"{i.token!r}: {i.message}" for i in r.issues)
        for r in reports
        if r.issues
    ]


def _check_issue_rate(kind: str, reports: list, limit: float) -> None:
    bad = _issue_lines(reports)
    rate = len(bad) / len(reports) if reports else 0.0
    assert rate <= limit, _report(
        f"{kind} issue rate {rate:.1%} exceeds {limit:.0%}", bad, len(reports)
    )


def _check_never_raises(kind: str, results: Tuple[list, List[str]]) -> None:
    reports, crashes = results
    total = len(reports) + len(crashes)
    assert not crashes, _report(f"parse_{kind.lower()} raised", crashes, total)


def _check_has_station(kind: str, reports: list) -> None:
    missing = [r.raw for r in reports if not r.station]
    assert not missing, _report(f"{kind} without station", missing, len(reports))


def test_metar_corpus_never_raises(metars: Tuple[list, List[str]]) -> None:
    _check_never_raises("METAR", metars)


def test_metar_corpus_has_station(metars: Tuple[list, List[str]]) -> None:
    _check_has_station("METAR", metars[0])


def test_metar_corpus_issue_rate(metars: Tuple[list, List[str]]) -> None:
    _check_issue_rate("METAR", metars[0], METAR_MAX_ISSUE_RATE)


def test_taf_corpus_never_raises(tafs: Tuple[list, List[str]]) -> None:
    _check_never_raises("TAF", tafs)


def test_taf_corpus_has_station(tafs: Tuple[list, List[str]]) -> None:
    _check_has_station("TAF", tafs[0])


def test_taf_corpus_issue_rate(tafs: Tuple[list, List[str]]) -> None:
    _check_issue_rate("TAF", tafs[0], TAF_MAX_ISSUE_RATE)
