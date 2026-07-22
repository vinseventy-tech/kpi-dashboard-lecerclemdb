from __future__ import annotations

from datetime import date
from typing import Any

from ..models import KpiSnapshot
from ..periods import Period


def _parse_metricool_date(value: Any) -> date | None:
    if isinstance(value, dict):
        value = value.get("dateTime") or value.get("date") or value.get("timestamp")
    if not value:
        return None
    raw = str(value)
    if len(raw) >= 10:
        try:
            return date.fromisoformat(raw[:10])
        except ValueError:
            return None
    return None


def _post_day(post: dict[str, Any]) -> date | None:
    for key in ["created", "date", "published", "publicationDate", "createdAt"]:
        day = _parse_metricool_date(post.get(key))
        if day:
            return day
    return None


def _post_engagement(post: dict[str, Any]) -> float | None:
    value = post.get("engagement")
    if value not in (None, ""):
        return float(value)
    impressions = float(post.get("impressions") or 0)
    if not impressions:
        return None
    interactions = sum(
        float(post.get(key) or 0)
        for key in [
            "clicks",
            "comments",
            "likes",
            "shares",
            "like",
            "praise",
            "entertainment",
            "empathy",
            "interest",
            "appreciation",
        ]
    )
    return interactions / impressions


def _is_matching_post(post: dict[str, Any], account: dict[str, Any]) -> bool:
    company_id = str(account.get("company_id") or "").strip()
    if not company_id:
        return True
    return str(post.get("companyId") or "").strip() == company_id


def compute_linkedin_weekly_snapshots(
    posts_by_owner: dict[str, list[dict[str, Any]]],
    periods: list[Period],
    accounts: list[dict[str, Any]],
) -> list[KpiSnapshot]:
    period_by_day: dict[date, Period] = {}
    for period in periods:
        current = period.start
        while current <= period.end:
            period_by_day[current] = period
            current = date.fromordinal(current.toordinal() + 1)

    post_counts: dict[tuple[date, str], float] = {}
    total_counts: dict[date, float] = {period.start: 0.0 for period in periods}
    engagements: dict[date, list[float]] = {period.start: [] for period in periods}

    owners = [str(account["owner"]) for account in accounts]
    for owner in owners:
        for period in periods:
            post_counts[(period.start, owner)] = 0.0

    for account in accounts:
        owner = str(account["owner"])
        for post in posts_by_owner.get(owner, []):
            if not _is_matching_post(post, account):
                continue
            day = _post_day(post)
            if day is None:
                continue
            period = period_by_day.get(day)
            if period is None:
                continue
            post_counts[(period.start, owner)] += 1
            total_counts[period.start] += 1
            engagement = _post_engagement(post)
            if engagement is not None:
                engagements[period.start].append(engagement)

    snapshots: list[KpiSnapshot] = []
    for period in periods:
        for account in accounts:
            owner = str(account["owner"])
            snapshots.append(
                KpiSnapshot(
                    kpi_code="linkedin_posts_count_weekly",
                    kpi_name="Posts LinkedIn publies",
                    period_type="week",
                    period_start=period.start,
                    period_end=period.end,
                    value=post_counts[(period.start, owner)],
                    unit="count",
                    source="metricool",
                    segment="linkedin",
                    owner=owner,
                    dimension_1=f"profile={account.get('label', owner)}",
                    source_record_count=len(posts_by_owner.get(owner, [])),
                )
            )
        snapshots.append(
            KpiSnapshot(
                kpi_code="linkedin_posts_count_weekly",
                kpi_name="Posts LinkedIn publies",
                period_type="week",
                period_start=period.start,
                period_end=period.end,
                value=total_counts[period.start],
                unit="count",
                source="metricool",
                segment="linkedin",
                owner="total",
                dimension_1="profile=total",
                source_record_count=int(total_counts[period.start]),
            )
        )
        week_engagements = engagements[period.start]
        snapshots.append(
            KpiSnapshot(
                kpi_code="linkedin_average_engagement_rate_weekly",
                kpi_name="Taux d'engagement moyen LinkedIn",
                period_type="week",
                period_start=period.start,
                period_end=period.end,
                value=sum(week_engagements) / len(week_engagements) if week_engagements else 0.0,
                unit="percent",
                source="metricool",
                segment="linkedin",
                owner="total",
                dimension_1=f"posts_with_engagement={len(week_engagements)}",
                source_record_count=len(week_engagements),
            )
        )
    return snapshots
