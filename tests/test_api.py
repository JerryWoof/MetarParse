from metar_parse import Metar, Taf, parse


def test_parse_dispatches_on_report_type():
    assert isinstance(parse("METAR LKPR 121630Z 24012KT 9999 FEW020 14/09 Q1012"), Metar)
    assert isinstance(parse("KJFK 121651Z 31015KT 10SM FEW050 18/06 A2992"), Metar)
    assert isinstance(parse("TAF LKPR 121100Z 1212/1312 24012KT 9999 FEW030"), Taf)
    assert isinstance(parse(""), Metar)
