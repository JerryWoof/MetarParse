from datetime import datetime, timedelta, timezone

import pytest

from metar_parse.models import DayTime

UTC = timezone.utc


def ref(*args):
    return datetime(*args, tzinfo=UTC)


@pytest.mark.parametrize(
    "dt, reference, expected",
    [
        # same day
        (DayTime(12, 14, 30), ref(2026, 10, 12, 14, 35), ref(2026, 10, 12, 14, 30)),
        # day 31 referenced on the 1st of the next month
        (DayTime(31, 23, 50), ref(2026, 11, 1, 0, 5), ref(2026, 10, 31, 23, 50)),
        # year rollover backwards
        (DayTime(31, 22, 0), ref(2027, 1, 1, 1, 0), ref(2026, 12, 31, 22, 0)),
        # year rollover forwards (TAF validity end)
        (DayTime(1, 6, 0), ref(2026, 12, 31, 18, 0), ref(2027, 1, 1, 6, 0)),
        # hour 24 means 00:00 of the next day
        (DayTime(12, 24, 0), ref(2026, 10, 12, 5, 0), ref(2026, 10, 13, 0, 0)),
        (DayTime(31, 24, 0), ref(2026, 10, 31, 5, 0), ref(2026, 11, 1, 0, 0)),
        (DayTime(28, 24, 0), ref(2026, 2, 28, 5, 0), ref(2026, 3, 1, 0, 0)),
        # leap day
        (DayTime(29, 12, 0), ref(2028, 3, 1, 0, 0), ref(2028, 2, 29, 12, 0)),
    ],
)
def test_to_datetime(dt, reference, expected):
    result = dt.to_datetime(reference)
    assert result == expected
    assert result.tzinfo == UTC


def test_to_datetime_without_day_picks_nearest_day():
    assert DayTime(None, 23, 55).to_datetime(ref(2026, 10, 12, 0, 10)) == ref(2026, 10, 11, 23, 55)
    assert DayTime(None, 0, 5).to_datetime(ref(2026, 10, 12, 23, 50)) == ref(2026, 10, 13, 0, 5)
    assert DayTime(None, 14, 0).to_datetime(ref(2026, 10, 12, 13, 0)) == ref(2026, 10, 12, 14, 0)


def test_to_datetime_without_hour_picks_nearest_hour():
    assert DayTime(None, None, 55).to_datetime(ref(2026, 10, 12, 15, 5)) == ref(
        2026, 10, 12, 14, 55
    )
    assert DayTime(None, None, 10).to_datetime(ref(2026, 10, 12, 15, 5)) == ref(
        2026, 10, 12, 15, 10
    )


def test_to_datetime_naive_and_other_timezone_reference():
    assert DayTime(12, 14, 30).to_datetime(datetime(2026, 10, 12, 15, 0)) == ref(
        2026, 10, 12, 14, 30
    )
    cet = timezone(timedelta(hours=2))
    reference = datetime(2026, 10, 13, 1, 0, tzinfo=cet)  # 2026-10-12 23:00Z
    assert DayTime(12, 23, 0).to_datetime(reference) == ref(2026, 10, 12, 23, 0)


@pytest.mark.parametrize(
    "dt",
    [
        DayTime(32, 12, 0),
        DayTime(0, 12, 0),
        DayTime(12, 25, 0),
        DayTime(12, 12, 60),
    ],
)
def test_to_datetime_invalid(dt):
    assert dt.to_datetime(ref(2026, 10, 12, 12, 0)) is None


def test_to_datetime_day_missing_from_month():
    # 30 February does not exist; neither January 30 nor March 30 is close.
    assert DayTime(30, 12, 0).to_datetime(ref(2026, 2, 28, 12, 0)) is None
    assert DayTime(31, 12, 0).to_datetime(ref(2026, 9, 30, 12, 0)) is None
    # 31 June does not exist, but 31 May is the nearest real date.
    assert DayTime(31, 12, 0).to_datetime(ref(2026, 6, 1, 0, 0)) == ref(2026, 5, 31, 12, 0)
