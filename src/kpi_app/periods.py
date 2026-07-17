from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone


@dataclass(frozen=True)
class Period:
    period_type: str
    start: date
    end: date


def parse_hubspot_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    if value.isdigit():
        return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc)
    normalized = value.replace("Z", "+00:00")
    return datetime.fromisoformat(normalized)


def week_period(day: date) -> Period:
    start = day - timedelta(days=day.weekday())
    return Period(period_type="week", start=start, end=start + timedelta(days=6))


def month_period(day: date) -> Period:
    start = day.replace(day=1)
    if start.month == 12:
        next_month = start.replace(year=start.year + 1, month=1)
    else:
        next_month = start.replace(month=start.month + 1)
    return Period(period_type="month", start=start, end=next_month - timedelta(days=1))


def rolling_week_periods(day: date, weeks: int) -> list[Period]:
    current = week_period(day)
    return [
        Period(
            period_type="week",
            start=current.start - timedelta(days=7 * offset),
            end=current.end - timedelta(days=7 * offset),
        )
        for offset in reversed(range(weeks))
    ]


def date_in_period(value: datetime | date | None, period: Period) -> bool:
    if value is None:
        return False
    day = value.date() if isinstance(value, datetime) else value
    return period.start <= day <= period.end
