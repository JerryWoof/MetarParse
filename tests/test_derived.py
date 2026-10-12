import pytest

from metar_parse.derived import flight_category, relative_humidity
from metar_parse.models import CloudLayer, Metar, Visibility


def make(vis=None, clouds=(), cavok=False):
    return Metar(raw="TEST", visibility=vis, clouds=list(clouds), cavok=cavok)


@pytest.mark.parametrize(
    "temp, dew, expected",
    [
        (20.0, 20.0, 100.0),
        (20.0, 10.0, 52.5),
        (14.0, 9.0, 71.8),
        (-2.0, -5.0, 79.9),
        (30.0, 25.0, 74.6),
    ],
)
def test_relative_humidity(temp, dew, expected):
    assert relative_humidity(temp, dew) == pytest.approx(expected, abs=0.1)


def test_relative_humidity_missing_and_capped():
    assert relative_humidity(None, 5.0) is None
    assert relative_humidity(5.0, None) is None
    assert relative_humidity(5.0, 6.0) == 100.0  # supersaturation reported as 100 %


def test_metar_property_uses_derived():
    m = Metar(raw="x", temperature_c=20.0, dewpoint_c=10.0)
    assert m.relative_humidity == relative_humidity(20.0, 10.0)


@pytest.mark.parametrize(
    "vis, clouds, cavok, expected",
    [
        (Visibility(9999), [CloudLayer("FEW", 2000)], False, "VFR"),
        (Visibility(9999), [CloudLayer("BKN", 3500)], False, "VFR"),
        (Visibility(9999), [CloudLayer("BKN", 3000)], False, "MVFR"),
        (Visibility(9999), [CloudLayer("OVC", 1000)], False, "MVFR"),
        (Visibility(9999), [CloudLayer("OVC", 900)], False, "IFR"),
        (Visibility(9999), [CloudLayer("BKN", 500)], False, "IFR"),
        (Visibility(9999), [CloudLayer("VV", 200)], False, "LIFR"),
        (Visibility(5, "SM"), [CloudLayer("SKC")], False, "MVFR"),
        (Visibility(3, "SM"), [], False, "MVFR"),
        (Visibility(2.5, "SM"), [], False, "IFR"),
        (Visibility(1, "SM"), [], False, "IFR"),
        (Visibility(0.5, "SM"), [], False, "LIFR"),
        (Visibility(1, "SM", modifier="M"), [], False, "LIFR"),
        (Visibility(6, "SM", modifier="P"), [CloudLayer("CLR")], False, "VFR"),
        (Visibility(4000), [CloudLayer("SCT", 1000)], False, "IFR"),  # 2.49 SM
        (Visibility(9000), [CloudLayer("SCT", 400)], False, "VFR"),  # SCT is no ceiling
        (Visibility(8000), [], False, "MVFR"),  # 4.97 SM
        (None, [], True, "VFR"),
        (None, [CloudLayer("OVC", 700)], False, "IFR"),
        # worst of visibility and ceiling wins
        (Visibility(0.75, "SM"), [CloudLayer("OVC", 2000)], False, "LIFR"),
        (Visibility(10, "SM"), [CloudLayer("FEW", 800), CloudLayer("OVC", 1200)], False, "MVFR"),
    ],
)
def test_flight_category(vis, clouds, cavok, expected):
    m = make(vis, clouds, cavok)
    assert flight_category(m) == expected
    assert m.flight_category == expected


def test_flight_category_unknown():
    assert flight_category(make()) is None
    assert flight_category(make(Visibility(None))) is None
    assert flight_category(make(Visibility(None), [CloudLayer("///")])) is None
