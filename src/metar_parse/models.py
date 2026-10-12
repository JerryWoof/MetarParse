"""Typed data model for decoded METAR, SPECI and TAF reports.

Every report keeps its ``raw`` text and a list of ``issues`` for tokens that
could not be decoded. Parsing is tolerant: a bad group produces an issue,
never an exception.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, List, Optional

KT_PER_MPS = 1.943844
KT_PER_KMH = 0.539957
M_PER_SM = 1609.344
HPA_PER_INHG = 33.8639


@dataclass
class ParseIssue:
    """A token the parser could not decode."""

    token: str
    message: str


@dataclass
class DayTime:
    """Day of month plus UTC time, as used in METAR/TAF groups (``121630Z``)."""

    day: Optional[int]
    hour: Optional[int]  # None when only minutes were reported (US remarks)
    minute: int = 0

    def __str__(self) -> str:
        day = f"{self.day:02d}" if self.day is not None else ""
        hour = f"{self.hour:02d}" if self.hour is not None else ""
        return f"{day}{hour}{self.minute:02d}Z"

    def to_datetime(self, reference: datetime) -> Optional[datetime]:
        """Resolve to the timezone-aware UTC datetime closest to ``reference``.

        Reports only carry day-of-month and time, so the month and year come
        from ``reference`` (typically the observation or issue time). Month and
        year rollover is handled: day 31 referenced on the 1st of the next month
        resolves to the previous month. Hour 24 means 00:00 of the next day. A
        missing day picks the nearest matching time of day; a missing hour the
        nearest matching minute. A naive ``reference`` is taken as UTC.

        Returns ``None`` for impossible values (minute 60, hour 25, day 32) or a
        day that does not exist within about half a month of ``reference``.

        >>> DayTime(31, 23, 50).to_datetime(datetime(2026, 11, 1, 0, 5))
        datetime.datetime(2026, 10, 31, 23, 50, tzinfo=datetime.timezone.utc)
        """
        if reference.tzinfo is None:
            reference = reference.replace(tzinfo=timezone.utc)
        else:
            reference = reference.astimezone(timezone.utc)
        if not 0 <= self.minute <= 59:
            return None
        if self.hour is not None and not 0 <= self.hour <= 24:
            return None
        if self.day is not None and not 1 <= self.day <= 31:
            return None

        candidates: List[datetime] = []
        if self.hour is None:
            base = reference.replace(minute=0, second=0, microsecond=0)
            for offset in (-1, 0, 1):
                when = base + timedelta(hours=offset, minutes=self.minute)
                if self.day is None or when.day == self.day:
                    candidates.append(when)
        elif self.day is None:
            base = reference.replace(hour=0, minute=0, second=0, microsecond=0)
            for offset in (-1, 0, 1):
                candidates.append(
                    base + timedelta(days=offset, hours=self.hour, minutes=self.minute)
                )
        else:
            for offset in (-1, 0, 1):
                month_index = reference.year * 12 + reference.month - 1 + offset
                year, month = divmod(month_index, 12)
                try:
                    midnight = datetime(year, month + 1, self.day, tzinfo=timezone.utc)
                except ValueError:  # e.g. 31 September
                    continue
                candidates.append(midnight + timedelta(hours=self.hour, minutes=self.minute))

        if not candidates:
            return None
        best = min(candidates, key=lambda c: abs(c - reference))
        if abs(best - reference) > timedelta(days=16):
            return None
        return best


@dataclass
class Wind:
    """Surface wind, e.g. ``24012G22KT`` or ``VRB03KT`` (with optional ``200V270``)."""

    direction_deg: Optional[int]
    speed: Optional[int]
    gust: Optional[int] = None
    unit: str = "KT"  # KT, MPS or KMH
    variable: bool = False  # VRB: direction too variable to report
    variable_from_deg: Optional[int] = None
    variable_to_deg: Optional[int] = None

    def _to_kt(self, value: Optional[int]) -> Optional[float]:
        if value is None:
            return None
        if self.unit == "MPS":
            return round(value * KT_PER_MPS, 1)
        if self.unit == "KMH":
            return round(value * KT_PER_KMH, 1)
        return float(value)

    @property
    def speed_kt(self) -> Optional[float]:
        return self._to_kt(self.speed)

    @property
    def gust_kt(self) -> Optional[float]:
        return self._to_kt(self.gust)

    @property
    def calm(self) -> bool:
        return self.speed == 0 and self.direction_deg in (0, None)


@dataclass
class Visibility:
    """Prevailing or directional visibility (``9999``, ``0800NE``, ``1 1/2SM``, ``P6SM``)."""

    value: Optional[float]
    unit: str = "M"  # M (metres) or SM (statute miles)
    modifier: Optional[str] = None  # "P" = more than, "M" = less than
    direction: Optional[str] = None  # e.g. "NE" for a directional minimum

    @property
    def meters(self) -> Optional[float]:
        if self.value is None:
            return None
        if self.unit == "SM":
            return round(self.value * M_PER_SM, 1)
        return float(self.value)

    @property
    def statute_miles(self) -> Optional[float]:
        if self.value is None:
            return None
        if self.unit == "SM":
            return float(self.value)
        return round(self.value / M_PER_SM, 2)


@dataclass
class RunwayVisualRange:
    """Runway visual range, e.g. ``R24/0600U`` or ``R10L/1000V1800FT``."""

    runway: str
    value: Optional[int]
    unit: str = "M"  # M or FT
    modifier: Optional[str] = None  # "P" above, "M" below the reportable range
    variable_high: Optional[int] = None
    variable_high_modifier: Optional[str] = None
    tendency: Optional[str] = None  # U (up), D (down), N (no change)


@dataclass
class WeatherPhenomenon:
    """Present or recent weather, e.g. ``-SHRA``, ``+TSRAGR``, ``VCFG``, ``RERA``."""

    raw: str
    intensity: str = ""  # "-", "+", "VC" or "" (moderate)
    descriptor: Optional[str] = None  # MI BC PR DR BL SH TS FZ
    phenomena: List[str] = field(default_factory=list)  # RA SN FG ...
    recent: bool = False


@dataclass
class CloudLayer:
    """Cloud layer, e.g. ``BKN035``, ``FEW020CB``, ``VV002``, ``SKC``."""

    cover: str  # FEW SCT BKN OVC VV SKC CLR NSC NCD ///
    height_ft: Optional[int] = None
    cloud_type: Optional[str] = None  # CB or TCU

    @property
    def is_ceiling(self) -> bool:
        return self.cover in ("BKN", "OVC", "VV") and self.height_ft is not None


@dataclass
class Pressure:
    """Altimeter / QNH, ``Q1012`` (hPa) or ``A2992`` (inHg)."""

    value: float
    unit: str = "hPa"  # hPa or inHg

    @property
    def hpa(self) -> float:
        if self.unit == "inHg":
            return round(self.value * HPA_PER_INHG, 1)
        return float(self.value)

    @property
    def inhg(self) -> float:
        if self.unit == "hPa":
            return round(self.value / HPA_PER_INHG, 2)
        return float(self.value)


@dataclass
class Forecast:
    """Weather conditions shared by METAR trend groups and TAF change groups."""

    wind: Optional[Wind] = None
    visibility: Optional[Visibility] = None
    weather: List[WeatherPhenomenon] = field(default_factory=list)
    clouds: List[CloudLayer] = field(default_factory=list)
    cavok: bool = False
    nsw: bool = False  # no significant weather (end of previous weather)


@dataclass
class TrendGroup(Forecast):
    """METAR landing trend: ``NOSIG``, ``BECMG FM1700 4000``, ``TEMPO TL1800 -RA``."""

    kind: str = "NOSIG"  # NOSIG, BECMG or TEMPO
    from_time: Optional[DayTime] = None  # FM
    until_time: Optional[DayTime] = None  # TL
    at_time: Optional[DayTime] = None  # AT
    raw: str = ""


@dataclass
class PressureTendency:
    """US remarks ``5appp`` group: 3-hour pressure tendency."""

    code: int  # WMO code table 0200, 0-8
    change_hpa: float


@dataclass
class PeakWind:
    """US remarks ``PK WND 28045/1955``."""

    direction_deg: int
    speed_kt: int
    time: DayTime


@dataclass
class Remarks:
    """Decoded ``RMK`` section (mostly US/ASOS conventions)."""

    raw: str = ""
    station_type: Optional[str] = None  # AO1 or AO2
    sea_level_pressure_hpa: Optional[float] = None  # SLPppp
    precise_temperature_c: Optional[float] = None  # Tsnnnsnnn
    precise_dewpoint_c: Optional[float] = None
    hourly_precip_in: Optional[float] = None  # Prrrr
    precip_3_6h_in: Optional[float] = None  # 6rrrr
    precip_24h_in: Optional[float] = None  # 7rrrr
    max_temp_6h_c: Optional[float] = None  # 1snnn
    min_temp_6h_c: Optional[float] = None  # 2snnn
    max_temp_24h_c: Optional[float] = None  # 4snnnsnnn
    min_temp_24h_c: Optional[float] = None
    pressure_tendency: Optional[PressureTendency] = None  # 5appp
    peak_wind: Optional[PeakWind] = None
    wind_shift: Optional[DayTime] = None  # WSHFT hhmm
    wind_shift_frontal: bool = False  # WSHFT ... FROPA
    snow_depth_in: Optional[int] = None  # 4/sss
    sunshine_minutes: Optional[int] = None  # 98mmm
    pressure_rising_rapidly: bool = False  # PRESRR
    pressure_falling_rapidly: bool = False  # PRESFR
    maintenance_needed: bool = False  # trailing $
    sensor_status: List[str] = field(default_factory=list)  # PWINO, TSNO, FZRANO ...
    lightning: List[str] = field(default_factory=list)  # free-text LTG groups
    unparsed: List[str] = field(default_factory=list)


@dataclass
class _Report:
    def to_dict(self) -> dict[str, Any]:
        """Plain dict, safe for ``json.dumps``."""
        return asdict(self)


@dataclass
class Metar(_Report):
    """A decoded METAR or SPECI observation."""

    raw: str
    report_type: str = "METAR"  # METAR or SPECI
    station: Optional[str] = None
    time: Optional[DayTime] = None
    auto: bool = False
    corrected: bool = False  # COR
    nil: bool = False
    wind: Optional[Wind] = None
    visibility: Optional[Visibility] = None
    min_visibility: Optional[Visibility] = None  # directional minimum, e.g. 1500SW
    runway_visual_range: List[RunwayVisualRange] = field(default_factory=list)
    weather: List[WeatherPhenomenon] = field(default_factory=list)
    recent_weather: List[WeatherPhenomenon] = field(default_factory=list)
    clouds: List[CloudLayer] = field(default_factory=list)
    cavok: bool = False
    temperature_c: Optional[float] = None
    dewpoint_c: Optional[float] = None
    pressure: Optional[Pressure] = None
    windshear: List[str] = field(default_factory=list)  # WS R24, WS ALL RWY
    runway_state: List[str] = field(default_factory=list)  # R24/290050 etc.
    color_state: Optional[str] = None  # military airfield colour code: BLU, WHT+, GRN ...
    sea_state: Optional[str] = None  # W12/S4, W18/H10 (offshore / coastal stations)
    trends: List[TrendGroup] = field(default_factory=list)
    remarks: Optional[Remarks] = None
    issues: List[ParseIssue] = field(default_factory=list)

    @property
    def ceiling_ft(self) -> Optional[int]:
        heights = [c.height_ft for c in self.clouds if c.is_ceiling]
        return min(heights) if heights else None  # type: ignore[type-var]

    @property
    def flight_category(self) -> Optional[str]:
        from .derived import flight_category

        return flight_category(self)

    @property
    def relative_humidity(self) -> Optional[float]:
        from .derived import relative_humidity

        return relative_humidity(self.temperature_c, self.dewpoint_c)


@dataclass
class TafChangeGroup(Forecast):
    """One period of a TAF: the base forecast or an ``FM``/``BECMG``/``TEMPO``/``PROB`` group."""

    kind: str = "BASE"  # BASE, FM, BECMG, TEMPO, INTER, PROB
    probability: Optional[int] = None  # 30 or 40 for PROB30 / PROB40 (also PROB30 TEMPO)
    valid_from: Optional[DayTime] = None
    valid_to: Optional[DayTime] = None
    raw: str = ""


@dataclass
class TafTemperature:
    """``TX25/1214Z`` or ``TN08/1305Z`` forecast extreme."""

    kind: str  # TX (max) or TN (min)
    temperature_c: int
    time: DayTime


@dataclass
class Taf(_Report):
    """A decoded Terminal Aerodrome Forecast."""

    raw: str
    station: Optional[str] = None
    issued: Optional[DayTime] = None
    valid_from: Optional[DayTime] = None
    valid_to: Optional[DayTime] = None
    amended: bool = False  # AMD
    corrected: bool = False  # COR
    nil: bool = False
    cancelled: bool = False  # CNL
    groups: List[TafChangeGroup] = field(default_factory=list)
    temperatures: List[TafTemperature] = field(default_factory=list)
    remarks_raw: Optional[str] = None
    issues: List[ParseIssue] = field(default_factory=list)
