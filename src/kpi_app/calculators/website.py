from __future__ import annotations

import csv
from datetime import date
from pathlib import Path
from typing import Iterable

from ..models import KpiSnapshot
from ..periods import Period, week_period


DATE_FIELDS = ["period_start", "week_start", "date", "semaine", "week"]
VALUE_FIELDS = ["visits", "sessions", "active_users", "activeUsers", "visites", "utilisateurs"]


def _first_present(row: dict[str, str], fields: Iterable[str]) -> str | None:
    normalized = {key.strip(): value for key, value in row.items()}
    lower_map = {key.lower(): key for key in normalized}
    for field in fields:
        key = lower_map.get(field.lower())
        if key is not None and str(normalized[key]).strip():
            return str(normalized[key]).strip()
    return None


def parse_visit_value(value: str) -> float:
    cleaned = value.strip().replace("\u00a0", " ").replace(" ", "")
    if "," in cleaned and "." not in cleaned:
        cleaned = cleaned.replace(",", ".")
    return float(cleaned)


def website_visit_snapshot(period_day: date, visits: float) -> KpiSnapshot:
    period = week_period(period_day)
    return website_visit_snapshot_for_period(period, visits)


def website_visit_snapshot_for_period(period: Period, visits: float) -> KpiSnapshot:
    return KpiSnapshot(
        kpi_code="website_visits_weekly",
        kpi_name="Visites site web",
        period_type="week",
        period_start=period.start,
        period_end=period.end,
        value=visits,
        unit="count",
        source="ga4",
        segment="website",
        dimension_1="domain=lecerclemdb.com",
        source_record_count=1,
    )


def read_website_visits_csv(path: Path) -> list[KpiSnapshot]:
    snapshots: list[KpiSnapshot] = []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        for index, row in enumerate(reader, start=2):
            raw_date = _first_present(row, DATE_FIELDS)
            raw_value = _first_present(row, VALUE_FIELDS)
            if raw_date is None or raw_value is None:
                raise ValueError(
                    f"Ligne {index}: colonnes attendues date/semaine et visites/sessions introuvables."
                )
            snapshots.append(website_visit_snapshot(date.fromisoformat(raw_date), parse_visit_value(raw_value)))
    return snapshots


def ga4_daily_rows_to_weekly_snapshots(rows: list[dict], periods: list[Period]) -> list[KpiSnapshot]:
    visits_by_week = {period.start: 0.0 for period in periods}
    period_by_day: dict[date, Period] = {}
    for period in periods:
        current = period.start
        while current <= period.end:
            period_by_day[current] = period
            current = date.fromordinal(current.toordinal() + 1)

    for row in rows:
        dimensions = row.get("dimensionValues", [])
        metrics = row.get("metricValues", [])
        if not dimensions or not metrics:
            continue
        raw_date = str(dimensions[0].get("value", ""))
        if len(raw_date) != 8:
            continue
        day = date.fromisoformat(f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:]}")
        period = period_by_day.get(day)
        if period is None:
            continue
        visits_by_week[period.start] += float(metrics[0].get("value", "0") or 0)

    return [website_visit_snapshot_for_period(period, visits_by_week[period.start]) for period in periods]
