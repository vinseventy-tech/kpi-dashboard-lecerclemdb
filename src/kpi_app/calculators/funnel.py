from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Any

from ..models import KpiSnapshot
from ..periods import Period, date_in_period, parse_hubspot_datetime


def property_value(record: dict[str, Any], name: str) -> Any:
    return record.get("properties", {}).get(name)


def normalize_source(raw_value: str | None, source_mapping: dict[str, list[str]]) -> str:
    if not raw_value:
        return "unassigned"
    value = raw_value.strip().lower()
    for normalized, aliases in source_mapping.items():
        if value in {alias.lower() for alias in aliases}:
            return normalized
    return value


def contact_key(contact: dict[str, Any], email_property: str = "email") -> str:
    contact_id = str(contact.get("id") or "").strip()
    if contact_id:
        return f"contact:{contact_id}"
    email = str(property_value(contact, email_property) or "").strip().lower()
    if email:
        return f"email:{email}"
    return "unknown"


def deal_member_key(
    deal: dict[str, Any],
    associated_contacts: dict[str, list[dict[str, Any]]],
    email_property: str = "email",
) -> str:
    deal_id = str(deal.get("id") or "")
    contacts = associated_contacts.get(deal_id, [])
    if contacts:
        return contact_key(contacts[0], email_property=email_property)
    return f"deal:{deal_id}"


def snapshots_for_count(
    kpi_code: str,
    kpi_name: str,
    period: Period,
    source: str,
    counts: dict[str, int],
    source_record_count: int,
    dimension_prefix: str,
    dedupe_method: str | None = None,
) -> list[KpiSnapshot]:
    return [
        KpiSnapshot(
            kpi_code=kpi_code,
            kpi_name=kpi_name,
            period_type=period.period_type,
            period_start=period.start,
            period_end=period.end,
            value=float(count),
            unit="count",
            source=source,
            segment=segment,
            dimension_1=f"{dimension_prefix}={segment}",
            source_record_count=source_record_count,
            dedupe_method=dedupe_method,
        )
        for segment, count in sorted(counts.items())
    ]


def compute_leads(
    contacts: list[dict[str, Any]],
    period: Period,
    config: dict[str, Any],
) -> list[KpiSnapshot]:
    contact_properties = config["hubspot"]["contact_properties"]
    source_mapping = config["hubspot"]["source_mapping"]
    created_property = "createdate"
    source_property = contact_properties["utm_source"]

    unique_by_source: dict[str, set[str]] = defaultdict(set)
    for contact in contacts:
        created_at = parse_hubspot_datetime(property_value(contact, created_property))
        if not date_in_period(created_at, period):
            continue
        source = normalize_source(property_value(contact, source_property), source_mapping)
        unique_by_source[source].add(contact_key(contact, contact_properties["email"]))

    return snapshots_for_count(
        "funnel_leads",
        "Leads",
        period,
        "hubspot",
        {source: len(keys) for source, keys in unique_by_source.items()},
        source_record_count=len(contacts),
        dimension_prefix="utm_source",
        dedupe_method="contact_id_then_email",
    )


def compute_sql(
    contacts: list[dict[str, Any]],
    period: Period,
    config: dict[str, Any],
) -> list[KpiSnapshot]:
    hs_config = config["hubspot"]
    contact_properties = hs_config["contact_properties"]
    source_mapping = hs_config["source_mapping"]
    sql_property = hs_config["sql"]["property"]
    sql_value = hs_config["sql"]["value"]

    unique_by_source: dict[str, set[str]] = defaultdict(set)
    for contact in contacts:
        became_sql = property_value(contact, sql_property) == sql_value
        if not became_sql:
            continue
        created_at = parse_hubspot_datetime(property_value(contact, "createdate"))
        if not date_in_period(created_at, period):
            continue
        source = normalize_source(property_value(contact, contact_properties["utm_source"]), source_mapping)
        unique_by_source[source].add(contact_key(contact, contact_properties["email"]))

    return snapshots_for_count(
        "funnel_sql",
        "SQL",
        period,
        "hubspot",
        {source: len(keys) for source, keys in unique_by_source.items()},
        source_record_count=len(contacts),
        dimension_prefix="utm_source",
        dedupe_method="contact_id_then_email",
    )


def compute_signed_members(
    contacts: list[dict[str, Any]],
    deals: list[dict[str, Any]],
    deal_contacts: dict[str, list[dict[str, Any]]],
    period: Period,
    config: dict[str, Any],
) -> list[KpiSnapshot]:
    hs_config = config["hubspot"]
    contact_properties = hs_config["contact_properties"]
    signed_config = hs_config["signed_member"]
    source_mapping = hs_config["source_mapping"]

    unique_by_source: dict[str, set[str]] = defaultdict(set)

    for contact in contacts:
        if property_value(contact, signed_config["contact_property"]) != signed_config["won_stage_id"]:
            continue
        created_at = parse_hubspot_datetime(property_value(contact, "createdate"))
        if not date_in_period(created_at, period):
            continue
        source = normalize_source(property_value(contact, contact_properties["utm_source"]), source_mapping)
        unique_by_source[source].add(contact_key(contact, contact_properties["email"]))

    for deal in deals:
        props = deal.get("properties", {})
        if props.get("pipeline") != signed_config["deal_pipeline_id"]:
            continue
        if props.get("dealstage") != signed_config["deal_stage_id"]:
            continue
        closed_at = parse_hubspot_datetime(props.get("closedate"))
        if not date_in_period(closed_at, period):
            continue
        key = deal_member_key(deal, deal_contacts, contact_properties["email"])
        contacts_for_deal = deal_contacts.get(str(deal.get("id")), [])
        source_contact = contacts_for_deal[0] if contacts_for_deal else {}
        source = normalize_source(property_value(source_contact, contact_properties["utm_source"]), source_mapping)
        unique_by_source[source].add(key)

    return snapshots_for_count(
        "funnel_signed_members",
        "Membres signes",
        period,
        "hubspot",
        {source: len(keys) for source, keys in unique_by_source.items()},
        source_record_count=len(contacts) + len(deals),
        dimension_prefix="utm_source",
        dedupe_method="contact_id_then_email_then_deal_id",
    )


def compute_conversion_rates(count_snapshots: list[KpiSnapshot]) -> list[KpiSnapshot]:
    by_segment: dict[str, dict[str, KpiSnapshot]] = defaultdict(dict)
    for snapshot in count_snapshots:
        by_segment[snapshot.segment or "unassigned"][snapshot.kpi_code] = snapshot

    rate_defs = [
        ("funnel_lead_to_mql_rate", "Taux Lead vers MQL", "funnel_mql", "funnel_leads"),
        ("funnel_mql_to_sql_rate", "Taux MQL vers SQL", "funnel_sql", "funnel_mql"),
        ("funnel_sql_to_member_rate", "Taux SQL vers membre signe", "funnel_signed_members", "funnel_sql"),
        ("funnel_lead_to_member_rate", "Taux Lead vers membre signe", "funnel_signed_members", "funnel_leads"),
    ]

    rates: list[KpiSnapshot] = []
    for segment, snapshots in by_segment.items():
        anchor = next(iter(snapshots.values()))
        for code, name, numerator_code, denominator_code in rate_defs:
            numerator = snapshots.get(numerator_code)
            denominator = snapshots.get(denominator_code)
            if not numerator or not denominator or denominator.value == 0:
                continue
            rates.append(
                KpiSnapshot(
                    kpi_code=code,
                    kpi_name=name,
                    period_type=anchor.period_type,
                    period_start=anchor.period_start,
                    period_end=anchor.period_end,
                    value=numerator.value / denominator.value,
                    unit="percent",
                    source="hubspot",
                    segment=segment,
                    dimension_1=f"utm_source={segment}",
                    source_record_count=anchor.source_record_count,
                )
            )
    return rates


def placeholder_mql_snapshot(period: Period, config: dict[str, Any]) -> list[KpiSnapshot]:
    missing_forms = [
        form["label"]
        for form in config["hubspot"]["mql"]["forms"]
        if not form.get("form_id") and not form.get("fallback_property")
    ]
    if not missing_forms:
        return []
    return [
        KpiSnapshot(
            kpi_code="funnel_mql",
            kpi_name="MQL",
            period_type=period.period_type,
            period_start=period.start,
            period_end=period.end,
            value=0,
            unit="count",
            source="hubspot",
            segment="needs_mapping",
            dimension_1="mapping_status=missing_form_ids",
            dimension_2=", ".join(missing_forms),
            source_record_count=0,
        )
    ]
