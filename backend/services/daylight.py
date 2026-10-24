"""Time-of-day awareness. Lighting concerns matter mainly after dark, so the lighting weight in the
score follows the sun: full weight at night, reduced at dusk and dawn, low in daylight.

Solar position uses the NOAA general solar position formulas, which are accurate to well under a degree
for this purpose.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone

# How much of the lighting weight applies in each period. Provisional values.
LIGHTING_MULTIPLIER = {"day": 0.25, "twilight": 0.7, "night": 1.0}


def solar_elevation_deg(lat: float, lng: float, when: datetime) -> float:
    """Sun elevation above the horizon in degrees (negative when the sun is below it)."""
    when = when.replace(tzinfo=timezone.utc) if when.tzinfo is None else when.astimezone(timezone.utc)
    day_of_year = when.timetuple().tm_yday
    hour = when.hour + when.minute / 60 + when.second / 3600
    g = 2 * math.pi / 365 * (day_of_year - 1 + (hour - 12) / 24)  # fractional year, radians
    eq_time = 229.18 * (
        0.000075 + 0.001868 * math.cos(g) - 0.032077 * math.sin(g)
        - 0.014615 * math.cos(2 * g) - 0.040849 * math.sin(2 * g)
    )  # minutes
    decl = (
        0.006918 - 0.399912 * math.cos(g) + 0.070257 * math.sin(g)
        - 0.006758 * math.cos(2 * g) + 0.000907 * math.sin(2 * g)
        - 0.002697 * math.cos(3 * g) + 0.00148 * math.sin(3 * g)
    )  # radians
    true_solar_minutes = hour * 60 + eq_time + 4 * lng
    hour_angle = math.radians(true_solar_minutes / 4 - 180)
    phi = math.radians(lat)
    cos_zenith = math.sin(phi) * math.sin(decl) + math.cos(phi) * math.cos(decl) * math.cos(hour_angle)
    return math.degrees(math.asin(max(-1.0, min(1.0, cos_zenith))))


def light_period(elevation_deg: float) -> str:
    if elevation_deg > 0:
        return "day"
    if elevation_deg >= -6:
        return "twilight"  # civil twilight
    return "night"


def describe(lat: float, lng: float, when: datetime) -> dict:
    elevation = solar_elevation_deg(lat, lng, when)
    period = light_period(elevation)
    return {
        "period": period,
        "sun_elevation_deg": round(elevation, 1),
        "lighting_weight_multiplier": LIGHTING_MULTIPLIER[period],
        "at": when.astimezone(timezone.utc).isoformat() if when.tzinfo else when.replace(tzinfo=timezone.utc).isoformat(),
    }
