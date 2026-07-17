from __future__ import annotations

from datetime import date
from typing import Any

from ..models import KpiSnapshot
from ..periods import Period, date_in_period, parse_hubspot_datetime


def _number(value: Any) -> float:
    if value is None or value == "":
        return 0.0
    return float(value)


def _email_text(email: dict[str, Any]) -> str:
    props = email.get("properties", email)
    values = [
        email.get("name"),
        email.get("subject"),
        email.get("state"),
        props.get("name") if isinstance(props, dict) else None,
        props.get("subject") if isinstance(props, dict) else None,
    ]
    return " ".join(str(value).lower() for value in values if value)


def is_newsletter_email(email: dict[str, Any], config: dict[str, Any]) -> bool:
    filters = config.get("newsletter", {}).get("email_filters", {})
    text_needles = [item.lower() for item in filters.get("include_text_contains", [])]
    excluded_states = {item.upper() for item in filters.get("exclude_states", [])}
    state = str(email.get("state") or "").upper()
    if state in excluded_states:
        return False
    if not text_needles:
        return True
    text = _email_text(email)
    return any(needle in text for needle in text_needles)


def compute_newsletter_email_stats(
    emails: list[dict[str, Any]],
    period: Period,
    config: dict[str, Any],
) -> list[KpiSnapshot]:
    fields = config["newsletter"]["statistics_fields"]
    delivered_field = fields["delivered"]
    opens_field = fields["unique_opens"]
    send_date_field = fields["send_date"]

    sent_count = 0
    delivered = 0.0
    opens = 0.0

    for email in emails:
        if not is_newsletter_email(email, config):
            continue
        props = email.get("properties", email)
        sent_at = parse_hubspot_datetime(str(props.get(send_date_field) or ""))
        if not date_in_period(sent_at, period):
            continue
        stats = email.get("statistics", props.get("statistics", {})) or {}
        email_delivered = _number(stats.get(delivered_field, props.get(delivered_field)))
        email_opens = _number(stats.get(opens_field, props.get(opens_field)))
        email_sent = _number(stats.get("sent", props.get("sent")))
        if email_sent or email_delivered or email_opens:
            sent_count += 1
        delivered += email_delivered
        opens += email_opens

    open_rate = opens / delivered if delivered else 0.0

    return [
        KpiSnapshot(
            kpi_code="newsletter_sent_count_weekly",
            kpi_name="Newsletters envoyees",
            period_type=period.period_type,
            period_start=period.start,
            period_end=period.end,
            value=float(sent_count),
            unit="count",
            source="hubspot",
            segment="newsletter",
            source_record_count=len(emails),
        ),
        KpiSnapshot(
            kpi_code="newsletter_open_rate_weekly",
            kpi_name="Taux d'ouverture newsletter",
            period_type=period.period_type,
            period_start=period.start,
            period_end=period.end,
            value=open_rate,
            unit="percent",
            source="hubspot",
            segment="newsletter",
            dimension_1=f"delivered={int(delivered)}",
            dimension_2=f"opens={int(opens)}",
            source_record_count=len(emails),
        ),
    ]


def compute_newsletter_subscribers_snapshot(
    active_subscriber_count: int | None,
    period: Period,
    source_record_count: int = 0,
    missing_reason: str | None = None,
) -> list[KpiSnapshot]:
    if active_subscriber_count is None:
        reason = missing_reason or "subscriber_count_not_collected"
        return [
            KpiSnapshot(
                kpi_code="newsletter_subscribers_total_weekly",
                kpi_name="Abonnes newsletter",
                period_type=period.period_type,
                period_start=period.start,
                period_end=period.end,
                value=0,
                unit="count",
                source="hubspot",
                segment="needs_mapping" if reason == "missing_newsletter_list_id" else "newsletter",
                dimension_1=f"collection_status={reason}",
                source_record_count=0,
            )
        ]

    return [
        KpiSnapshot(
            kpi_code="newsletter_subscribers_total_weekly",
            kpi_name="Abonnes newsletter",
            period_type=period.period_type,
            period_start=period.start,
            period_end=period.end,
            value=float(active_subscriber_count),
            unit="count",
            source="hubspot",
            segment="newsletter",
            source_record_count=source_record_count,
            dedupe_method="contact_id",
        )
    ]
