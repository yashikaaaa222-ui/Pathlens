from datetime import datetime, timezone

from services import daylight


def test_equinox_noon_in_london_is_high_and_midnight_is_deep_night():
    noon = daylight.solar_elevation_deg(51.5074, -0.1278, datetime(2026, 3, 20, 12, 0, tzinfo=timezone.utc))
    midnight = daylight.solar_elevation_deg(51.5074, -0.1278, datetime(2026, 3, 20, 0, 0, tzinfo=timezone.utc))
    assert 36 < noon < 40  # 90 - latitude at the equinox
    assert midnight < -30
    assert daylight.light_period(noon) == "day" and daylight.light_period(midnight) == "night"


def test_summer_is_brighter_than_winter_at_the_same_hour():
    summer = daylight.solar_elevation_deg(51.5, -0.1, datetime(2026, 6, 21, 12, 0, tzinfo=timezone.utc))
    winter = daylight.solar_elevation_deg(51.5, -0.1, datetime(2026, 12, 21, 12, 0, tzinfo=timezone.utc))
    assert summer - winter > 40


def test_period_boundaries():
    assert daylight.light_period(0.1) == "day"
    assert daylight.light_period(-3) == "twilight"
    assert daylight.light_period(-7) == "night"


def test_describe_gives_the_lighting_multiplier_and_treats_naive_times_as_utc():
    night = daylight.describe(51.5, -0.1, datetime(2026, 12, 15, 22, 0))
    day = daylight.describe(51.5, -0.1, datetime(2026, 6, 21, 12, 0, tzinfo=timezone.utc))
    assert night["period"] == "night" and night["lighting_weight_multiplier"] == 1.0
    assert day["period"] == "day" and day["lighting_weight_multiplier"] < night["lighting_weight_multiplier"]
