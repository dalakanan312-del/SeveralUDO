from __future__ import annotations

from datetime import date, timedelta


QUARTER_RANGES = ("Jan 1–Mar 31", "Apr 1–Jun 30", "Jul 1–Sep 30", "Oct 1–Dec 31")


def global_day_parts(global_day: int, start_year: int, days_per_year: int) -> tuple[int, int]:
    day = int(global_day)
    per_year = max(1, int(days_per_year))
    return int(start_year) + (day - 1) // per_year, ((day - 1) % per_year) + 1


def date_range_label(global_day: int, start_year: int, days_per_year: int) -> str:
    year, challenge_day = global_day_parts(global_day, start_year, days_per_year)
    if int(days_per_year) == 4:
        return f"{QUARTER_RANGES[challenge_day - 1]}, {year}"
    return f"Year {year}, challenge day {challenge_day}"


def exact_historical_date(global_day: int, hour: int, minute: int, start_year: int, days_per_year: int) -> date | None:
    """Convert a Sims clock time into an exact date for four-day challenge years."""
    if int(days_per_year) != 4:
        return None
    year, challenge_day = global_day_parts(global_day, start_year, days_per_year)
    start_month = (challenge_day - 1) * 3 + 1
    quarter_start = date(year, start_month, 1)
    quarter_end = date(year + 1, 1, 1) if start_month == 10 else date(year, start_month + 3, 1)
    quarter_days = (quarter_end - quarter_start).days
    minutes = max(0, min(1439, int(hour) * 60 + int(minute)))
    offset = min(quarter_days - 1, (minutes * quarter_days) // 1440)
    return quarter_start + timedelta(days=offset)


def format_historical_date(value: date | None) -> str:
    return f"{value.strftime('%B')} {value.day}, {value.year}" if value else ""


def exact_historical_label(global_day: int, hour: int, minute: int, start_year: int, days_per_year: int) -> str:
    return format_historical_date(exact_historical_date(global_day, hour, minute, start_year, days_per_year))
