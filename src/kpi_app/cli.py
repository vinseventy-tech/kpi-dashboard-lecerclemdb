from __future__ import annotations

import argparse
import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from .calculators.funnel import (
    compute_conversion_rates,
    compute_leads,
    compute_signed_members,
    compute_sql,
    placeholder_mql_snapshot,
)
from .calculators.newsletter import (
    compute_newsletter_email_stats,
    compute_newsletter_subscribers_snapshot,
)
from .calculators.linkedin import compute_linkedin_weekly_snapshots
from .calculators.website import read_website_visits_csv
from .calculators.website import ga4_daily_rows_to_weekly_snapshots
from .config import (
    load_config,
    load_settings,
    require_ga4_property_id,
    require_google_credentials,
    require_metricool_user_token,
    require_token,
)
from .ga4_client import Ga4Client, hostname_filter
from .hubspot_client import HubSpotClient, HubSpotListClient, HubSpotMarketingEmailClient, HubSpotSchemaClient
from .hubspot_kpi_sync import kpi_snapshot_schema_payload, upsert_snapshot_to_hubspot
from .metricool_client import MetricoolClient
from .models import KpiSnapshot
from .periods import Period, month_period, rolling_week_periods, week_period
from .storage import compact_snapshot_ids, connect, init_db, latest_snapshots_for_kpi, latest_snapshots_for_kpi_series, upsert_snapshots
from .web import CHARTS, render_page, rows_as_dicts


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Collecte et calcule les KPIs HubSpot/Newsletter.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("init-db", help="Cree les tables SQLite.")
    subparsers.add_parser("compact-db", help="Nettoie les doublons de snapshots en base locale.")
    subparsers.add_parser("diagnose-newsletter", help="Teste les acces HubSpot necessaires aux KPIs newsletter.")
    subparsers.add_parser("diagnose-ga4", help="Teste les acces GA4 necessaires aux visites du site.")
    subparsers.add_parser("diagnose-metricool", help="Teste les acces Metricool necessaires aux KPIs LinkedIn.")
    hostnames = subparsers.add_parser("diagnose-ga4-hostnames", help="Liste les hostnames vus dans GA4, sans filtre de domaine.")
    hostnames.add_argument("--days", type=int, default=90, help="Nombre de jours a inspecter.")
    subparsers.add_parser("diagnose-kpi-schema", help="Verifie si l'objet HubSpot KPI Snapshot existe.")
    subparsers.add_parser("create-kpi-schema", help="Cree l'objet custom HubSpot KPI Snapshot.")

    backfill = subparsers.add_parser("backfill-newsletter", help="Calcule les KPIs newsletter sur les semaines glissantes.")
    backfill.add_argument("--weeks", type=int, default=52)
    backfill.add_argument("--date", default=date.today().isoformat(), help="Date de fin de fenetre, format YYYY-MM-DD.")
    backfill.add_argument("--dry-run", action="store_true", help="Calcule sans appeler HubSpot.")
    backfill.add_argument("--save", action="store_true", help="Sauvegarde les snapshots en SQLite.")

    website = subparsers.add_parser("import-website-visits", help="Importe les visites hebdomadaires du site depuis un CSV GA4/Sheets.")
    website.add_argument("--csv", required=True, help="Chemin du fichier CSV contenant une date/semaine et les visites/sessions.")
    website.add_argument("--save", action="store_true", help="Sauvegarde les snapshots en SQLite.")

    website_backfill = subparsers.add_parser("backfill-website", help="Recupere les visites du site depuis GA4 sur les semaines glissantes.")
    website_backfill.add_argument("--weeks", type=int, default=52)
    website_backfill.add_argument("--date", default=date.today().isoformat(), help="Date de fin de fenetre, format YYYY-MM-DD.")
    website_backfill.add_argument("--save", action="store_true", help="Sauvegarde les snapshots en SQLite.")

    linkedin_backfill = subparsers.add_parser("backfill-linkedin", help="Recupere les posts LinkedIn depuis Metricool sur les semaines glissantes.")
    linkedin_backfill.add_argument("--weeks", type=int, default=52)
    linkedin_backfill.add_argument("--date", default=date.today().isoformat(), help="Date de fin de fenetre, format YYYY-MM-DD.")
    linkedin_backfill.add_argument("--save", action="store_true", help="Sauvegarde les snapshots en SQLite.")

    export_static = subparsers.add_parser("export-static", help="Genere les pages HTML statiques pour GitHub Pages.")
    export_static.add_argument("--output", default="./github-pages-kpi-dashboard", help="Dossier de sortie des fichiers HTML.")
    export_static.add_argument("--weeks", type=int, default=52, help="Nombre de semaines a afficher.")

    for command in ["run-hubspot", "run-newsletter", "run-all"]:
        sub = subparsers.add_parser(command)
        sub.add_argument("--period", choices=["week", "month"], default="week")
        sub.add_argument("--date", default=date.today().isoformat(), help="Date dans la periode a calculer, format YYYY-MM-DD.")
        sub.add_argument("--dry-run", action="store_true", help="Affiche les snapshots sans appeler HubSpot.")
        sub.add_argument("--save", action="store_true", help="Sauvegarde les snapshots en SQLite.")
        sub.add_argument("--sync-hubspot", action="store_true", help="Pousse les snapshots vers l'objet KPI Snapshot HubSpot.")

    return parser


def selected_period(period_type: str, value: str) -> Period:
    day = date.fromisoformat(value)
    return week_period(day) if period_type == "week" else month_period(day)


def hubspot_client() -> HubSpotClient:
    settings = load_settings()
    return HubSpotClient(
        access_token=require_token(settings),
        base_url=settings.hubspot_base_url,
    )


def fetch_contacts(client: HubSpotClient, config: dict[str, Any]) -> list[dict[str, Any]]:
    contact_props = config["hubspot"]["contact_properties"]
    properties = sorted(
        {
            "createdate",
            contact_props["email"],
            contact_props["utm_source"],
            contact_props["fallback_analytics_source"],
            contact_props["fallback_analytics_source_detail"],
            contact_props["lifecycle_stage"],
            contact_props["member_stage"],
            contact_props["email_domain"],
            contact_props["email_optout"],
        }
    )
    return client.search_objects("contacts", properties=properties)


def fetch_won_deals(client: HubSpotClient, config: dict[str, Any]) -> list[dict[str, Any]]:
    signed_config = config["hubspot"]["signed_member"]
    return client.search_objects(
        "deals",
        properties=["pipeline", "dealstage", "closedate"],
        filters=[
            {"propertyName": "pipeline", "operator": "EQ", "value": signed_config["deal_pipeline_id"]},
            {"propertyName": "dealstage", "operator": "EQ", "value": signed_config["deal_stage_id"]},
        ],
    )


def fetch_deal_contacts(client: HubSpotClient, deals: list[dict[str, Any]], config: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    contact_props = config["hubspot"]["contact_properties"]
    properties = [contact_props["email"], contact_props["utm_source"]]
    output: dict[str, list[dict[str, Any]]] = {}
    for deal in deals:
        deal_id = str(deal["id"])
        associations = client.list_associations("deals", deal_id, "contacts")
        contact_ids = [str(item["toObjectId"]) for item in associations if item.get("toObjectId")]
        contacts = []
        for contact_id in contact_ids[:3]:
            response = client.request(
                "GET",
                f"/crm/v3/objects/contacts/{contact_id}",
                query={"properties": properties},
            )
            contacts.append(response)
        output[deal_id] = contacts
    return output


def compute_hubspot_snapshots(period: Period, config: dict[str, Any], dry_run: bool) -> list[KpiSnapshot]:
    if dry_run:
        contacts: list[dict[str, Any]] = []
        deals: list[dict[str, Any]] = []
        deal_contacts: dict[str, list[dict[str, Any]]] = {}
    else:
        client = hubspot_client()
        contacts = fetch_contacts(client, config)
        deals = fetch_won_deals(client, config)
        deal_contacts = fetch_deal_contacts(client, deals, config)

    count_snapshots = [
        *compute_leads(contacts, period, config),
        *placeholder_mql_snapshot(period, config),
        *compute_sql(contacts, period, config),
        *compute_signed_members(contacts, deals, deal_contacts, period, config),
    ]
    return [*count_snapshots, *compute_conversion_rates(count_snapshots)]


def fetch_newsletter_source_data(config: dict[str, Any], dry_run: bool) -> tuple[list[dict[str, Any]], int | None, str | None]:
    list_id = config["newsletter"]["list"]["id"]
    legacy_list_id = config["newsletter"]["list"].get("legacy_v1_id")
    subscriber_count = None
    subscriber_count_missing_reason = "missing_newsletter_list_id" if not list_id else "dry_run"

    if dry_run:
        emails: list[dict[str, Any]] = []
    else:
        client = hubspot_client()
        list_client = HubSpotListClient(client)
        email_client = HubSpotMarketingEmailClient(client)
        emails = []
        for email in email_client.list_emails():
            try:
                email["statistics"] = email_client.get_email_statistics(str(email.get("id")))
            except RuntimeError as error:
                email = _attach_legacy_campaign_counters(email_client, email)
                if not email.get("statistics"):
                    email["statistics_error"] = str(error)
            emails.append(email)

        if list_id:
            try:
                subscriber_count = list_client.count_segment_memberships(str(list_id))
                subscriber_count_missing_reason = None
            except RuntimeError:
                if not legacy_list_id:
                    raise
                subscriber_count = list_client.count_legacy_list_contacts(str(legacy_list_id))
                subscriber_count_missing_reason = None

    return emails, subscriber_count, subscriber_count_missing_reason


def compute_newsletter_snapshots(period: Period, config: dict[str, Any], dry_run: bool) -> list[KpiSnapshot]:
    emails, subscriber_count, subscriber_count_missing_reason = fetch_newsletter_source_data(config, dry_run)
    return [
        *compute_newsletter_subscribers_snapshot(
            subscriber_count,
            period,
            source_record_count=subscriber_count or 0,
            missing_reason=subscriber_count_missing_reason,
        ),
        *compute_newsletter_email_stats(emails, period, config),
    ]


def compute_newsletter_backfill(day: date, weeks: int, config: dict[str, Any], dry_run: bool = False) -> list[KpiSnapshot]:
    emails, subscriber_count, subscriber_count_missing_reason = fetch_newsletter_source_data(config, dry_run)
    periods = rolling_week_periods(day, weeks)
    snapshots: list[KpiSnapshot] = []
    current_week = week_period(day)
    for period in periods:
        if period.start == current_week.start:
            snapshots.extend(
                compute_newsletter_subscribers_snapshot(
                    subscriber_count,
                    period,
                    source_record_count=subscriber_count or 0,
                    missing_reason=subscriber_count_missing_reason,
                )
            )
        snapshots.extend(compute_newsletter_email_stats(emails, period, config))
    return snapshots


def ga4_client() -> tuple[Ga4Client, str]:
    settings = load_settings()
    return Ga4Client(require_google_credentials(settings), timeout_seconds=settings.ga4_timeout_seconds), require_ga4_property_id(settings)


def metricool_client() -> MetricoolClient:
    settings = load_settings()
    return MetricoolClient(
        user_token=require_metricool_user_token(settings),
        base_url=settings.metricool_base_url,
        timeout_seconds=settings.metricool_timeout_seconds,
    )


def website_ga4_payload(periods: list[Period], config: dict[str, Any]) -> dict[str, Any]:
    website_config = config.get("website", {})
    payload: dict[str, Any] = {
        "dateRanges": [
            {
                "startDate": periods[0].start.isoformat(),
                "endDate": periods[-1].end.isoformat(),
            }
        ],
        "dimensions": [{"name": "date"}, {"name": "hostName"}],
        "metrics": [{"name": website_config.get("metric", "sessions")}],
        "limit": 100000,
    }
    dimension_filter = hostname_filter(website_config.get("hostnames", []))
    if dimension_filter:
        payload["dimensionFilter"] = dimension_filter
    return payload


def compute_website_backfill(day: date, weeks: int, config: dict[str, Any]) -> list[KpiSnapshot]:
    periods = rolling_week_periods(day, weeks)
    client, property_id = ga4_client()
    report = client.run_report(property_id, website_ga4_payload(periods, config))
    return ga4_daily_rows_to_weekly_snapshots(report.get("rows", []), periods)


def metricool_linkedin_accounts(config: dict[str, Any]) -> list[dict[str, Any]]:
    accounts = config.get("linkedin", {}).get("metricool_accounts", [])
    return [account for account in accounts if account.get("owner") and account.get("user_id") and account.get("blog_id")]


def compute_linkedin_backfill(day: date, weeks: int, config: dict[str, Any]) -> list[KpiSnapshot]:
    periods = rolling_week_periods(day, weeks)
    accounts = metricool_linkedin_accounts(config)
    client = metricool_client()
    settings = load_settings()
    posts_by_owner: dict[str, list[dict[str, Any]]] = {}
    for account in accounts:
        owner = str(account["owner"])
        posts_by_owner[owner] = client.linkedin_posts(
            user_id=str(account["user_id"]),
            blog_id=str(account["blog_id"]),
            start_date=periods[0].start.isoformat(),
            end_date=periods[-1].end.isoformat(),
            timezone=settings.metricool_timezone,
        )
    return compute_linkedin_weekly_snapshots(posts_by_owner, periods, accounts)


def diagnose_metricool(config: dict[str, Any]) -> list[dict[str, Any]]:
    settings = load_settings()
    token = require_metricool_user_token(settings)
    accounts = metricool_linkedin_accounts(config)
    results = [
        _diagnostic_result("Metricool token", bool(token), "Token renseigne."),
        _diagnostic_result("Metricool LinkedIn accounts", bool(accounts), f"{len(accounts)} compte(s) LinkedIn configure(s)."),
    ]
    if not accounts:
        return results
    client = metricool_client()
    today = date.today()
    period = week_period(today)
    account = accounts[0]
    posts = client.linkedin_posts(
        user_id=str(account["user_id"]),
        blog_id=str(account["blog_id"]),
        start_date=period.start.isoformat(),
        end_date=period.end.isoformat(),
        timezone=settings.metricool_timezone,
    )
    keys = sorted(posts[0].keys()) if posts else []
    return [
        *results,
        _diagnostic_result(
            "Metricool LinkedIn posts",
            True,
            f"Connexion OK. {len(posts)} post(s) trouve(s) pour {account.get('label', account.get('owner'))}. Cles: {', '.join(keys[:20]) if keys else 'aucun post cette semaine'}",
        ),
    ]


def diagnose_ga4(config: dict[str, Any]) -> list[dict[str, Any]]:
    settings = load_settings()
    property_id = require_ga4_property_id(settings)
    credentials_path = require_google_credentials(settings)
    results = [
        _diagnostic_result("GA4 property ID", True, f"ID propriete renseigne: {property_id}."),
    ]
    if not credentials_path.exists():
        return [
            *results,
            _diagnostic_result("Google credentials file", False, f"Fichier introuvable: {credentials_path}."),
        ]
    credentials = json.loads(credentials_path.read_text(encoding="utf-8"))
    client_email = credentials.get("client_email")
    if not client_email:
        return [
            *results,
            _diagnostic_result("Google credentials file", False, "Le fichier JSON ne contient pas client_email."),
        ]
    results.append(_diagnostic_result("Google credentials file", True, f"Fichier JSON trouve. Compte de service: {client_email}."))

    periods = rolling_week_periods(date.today(), 1)
    client = Ga4Client(credentials_path, timeout_seconds=settings.ga4_timeout_seconds)
    payload = website_ga4_payload(periods, config)
    payload["limit"] = 1
    report = client.run_report(property_id, payload)
    rows = report.get("rows", [])
    row_count = report.get("rowCount", len(rows))
    return [
        *results,
        _diagnostic_result(
            "GA4 website visits",
            True,
            f"Connexion OK. Propriete {property_id}, {row_count} ligne(s) trouvee(s) pour la semaine test.",
        )
    ]


def diagnose_ga4_hostnames(days: int) -> list[dict[str, Any]]:
    settings = load_settings()
    property_id = require_ga4_property_id(settings)
    credentials_path = require_google_credentials(settings)
    client = Ga4Client(credentials_path, timeout_seconds=settings.ga4_timeout_seconds)
    end = date.today()
    start = end - timedelta(days=days)

    hostname_report = client.run_report(
        property_id,
        {
            "dateRanges": [{"startDate": start.isoformat(), "endDate": end.isoformat()}],
            "dimensions": [{"name": "hostName"}],
            "metrics": [{"name": "sessions"}],
            "orderBys": [{"metric": {"metricName": "sessions"}, "desc": True}],
            "limit": 50,
        },
    )
    hostnames = []
    total_sessions = 0
    for row in hostname_report.get("rows", []):
        hostname = row["dimensionValues"][0].get("value", "")
        sessions = row["metricValues"][0].get("value", "0")
        total_sessions += int(float(sessions or 0))
        hostnames.append(f"{hostname}={sessions}")

    return [
        _diagnostic_result(
            "GA4 total sessions",
            True,
            f"{total_sessions} session(s) trouvee(s) sur {start.isoformat()} -> {end.isoformat()}, sans filtre hostname.",
        ),
        _diagnostic_result(
            "GA4 hostnames",
            bool(hostnames),
            " | ".join(hostnames) if hostnames else "Aucun hostname trouve sur cette periode.",
        ),
    ]


def _diagnostic_result(name: str, ok: bool, detail: str) -> dict[str, Any]:
    return {"check": name, "ok": ok, "detail": detail}


def _interesting_paths(value: Any, prefix: str = "", max_results: int = 80) -> list[str]:
    needles = [
        "campaign",
        "stat",
        "open",
        "click",
        "deliver",
        "sent",
        "recipient",
        "bounce",
        "publish",
        "send",
        "email",
    ]
    matches: list[str] = []

    def walk(item: Any, path: str) -> None:
        if len(matches) >= max_results:
            return
        if isinstance(item, dict):
            for key, nested in item.items():
                next_path = f"{path}.{key}" if path else str(key)
                key_lower = str(key).lower()
                if any(needle in key_lower for needle in needles):
                    if isinstance(nested, (dict, list)):
                        preview = type(nested).__name__
                    else:
                        preview = str(nested)[:120]
                    matches.append(f"{next_path}={preview}")
                walk(nested, next_path)
        elif isinstance(item, list):
            for index, nested in enumerate(item[:5]):
                walk(nested, f"{path}[{index}]")

    walk(value, prefix)
    return matches


def _email_state(email: dict[str, Any]) -> str:
    return str(email.get("state") or email.get("status") or "").upper()


def _is_sent_email_candidate(email: dict[str, Any]) -> bool:
    state = _email_state(email)
    if state in {"DRAFT", "AUTOMATED_DRAFT"}:
        return False
    if email.get("isPublished") is True:
        return True
    if state in {"PUBLISHED", "SENT", "SCHEDULED", "PUBLISHED_OR_SCHEDULED"}:
        return True
    return False


def _find_sent_email_candidate(email_client: HubSpotMarketingEmailClient, max_pages: int = 5) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    after = None
    inspected: list[dict[str, Any]] = []
    for _ in range(max_pages):
        page = email_client.list_emails_page(limit=25, after=after)
        emails = page.get("results", [])
        inspected.extend(emails)
        for email in emails:
            if _is_sent_email_candidate(email):
                return email, inspected
        after = page.get("paging", {}).get("next", {}).get("after")
        if not after:
            break
    return None, inspected


def _email_campaign_ids(email: dict[str, Any]) -> list[str]:
    campaign_ids: list[str] = []
    for key in [
        "campaign",
        "campaignId",
        "campaign_id",
        "primaryEmailCampaignId",
        "emailCampaignGroupId",
    ]:
        if email.get(key):
            campaign_ids.append(str(email[key]))
    all_campaign_ids = email.get("allEmailCampaignIds")
    if isinstance(all_campaign_ids, list):
        campaign_ids.extend(str(item) for item in all_campaign_ids)
    properties = email.get("properties", {})
    if isinstance(properties, dict):
        for key in [
            "campaign",
            "campaignId",
            "campaign_id",
            "hs_email_campaign_id",
            "primaryEmailCampaignId",
            "emailCampaignGroupId",
        ]:
            if properties.get(key):
                campaign_ids.append(str(properties[key]))
    return list(dict.fromkeys(campaign_ids))


def _attach_legacy_campaign_counters(
    email_client: HubSpotMarketingEmailClient,
    email: dict[str, Any],
) -> dict[str, Any]:
    campaigns: list[dict[str, Any]] = []
    aggregate: dict[str, float] = {}
    for campaign_id in _email_campaign_ids(email):
        try:
            campaign = email_client.get_legacy_campaign(campaign_id)
        except RuntimeError:
            continue
        counters = campaign.get("counters")
        if isinstance(counters, dict):
            campaigns.append(campaign)
            for key, value in counters.items():
                if isinstance(value, (int, float)):
                    aggregate[key] = aggregate.get(key, 0) + value
    if not aggregate:
        return email
    return {
        **email,
        "statistics": aggregate,
        "legacyCampaignIds": [str(campaign.get("id")) for campaign in campaigns],
        "legacyCampaigns": campaigns,
    }


def diagnose_newsletter(config: dict[str, Any]) -> list[dict[str, Any]]:
    client = hubspot_client()
    email_client = HubSpotMarketingEmailClient(client)
    list_config = config["newsletter"]["list"]
    list_id = str(list_config["id"])
    legacy_list_id = str(list_config.get("legacy_v1_id") or "")
    results: list[dict[str, Any]] = []

    ok, response = client.try_request("GET", f"/crm/v3/lists/{list_id}/memberships", query={"limit": 1})
    if ok and isinstance(response, dict):
        count = len(response.get("results", []))
        results.append(_diagnostic_result("Segment ILS 19 memberships", True, f"Endpoint accessible, {count} resultat(s) sur la premiere page."))
    else:
        results.append(_diagnostic_result("Segment ILS 19 memberships", False, str(response)))

    if legacy_list_id:
        ok, response = client.try_request(
            "GET",
            f"/contacts/v1/lists/{legacy_list_id}/contacts/all",
            query={"count": 1},
        )
        if ok and isinstance(response, dict):
            contacts = len(response.get("contacts", []))
            results.append(_diagnostic_result("Legacy list V1 8 contacts", True, f"Endpoint accessible, {contacts} contact(s) sur la premiere page."))
        else:
            results.append(_diagnostic_result("Legacy list V1 8 contacts", False, str(response)))

    ok, response = client.try_request("GET", "/marketing/v3/emails", query={"limit": 1})
    first_email_id = None
    first_email = None
    if ok and isinstance(response, dict):
        first_page_emails = response.get("results", [])
        first_email = first_page_emails[0] if first_page_emails else None
        sent_candidate, inspected_emails = _find_sent_email_candidate(email_client)
        if sent_candidate:
            first_email = sent_candidate
        first_email_id = str(first_email.get("id")) if first_email else None
        if first_email:
            states: dict[str, int] = {}
            for email in inspected_emails:
                state = _email_state(email) or "UNKNOWN"
                states[state] = states.get(state, 0) + 1
            state_summary = ", ".join(f"{state}:{count}" for state, count in sorted(states.items()))
            keys = ", ".join(sorted(first_email.keys())[:20])
            properties = first_email.get("properties", {})
            property_keys = ", ".join(sorted(properties.keys())[:20]) if isinstance(properties, dict) else "n/a"
            interesting = " | ".join(_interesting_paths(first_email)[:30])
            results.append(
                _diagnostic_result(
                    "Marketing emails list",
                    True,
                    f"{len(first_page_emails)} email(s) sur la premiere page, {len(inspected_emails)} inspecte(s). Etats vus: {state_summary or 'aucun'}. Email teste {first_email_id} avec state={_email_state(first_email) or 'UNKNOWN'}. Cles: {keys}. Proprietes: {property_keys}. Champs utiles detectes: {interesting or 'aucun'}",
                )
            )
        else:
            results.append(_diagnostic_result("Marketing emails list", True, "0 email sur la premiere page."))
    else:
        results.append(_diagnostic_result("Marketing emails list", False, str(response)))

    if first_email_id:
        candidate_paths = [
            f"/marketing/v3/emails/{first_email_id}",
            f"/marketing/v3/emails/{first_email_id}/statistics",
            f"/email/public/v1/campaigns/{first_email_id}",
        ]
        campaign_ids: list[str] = []
        if isinstance(first_email, dict):
            campaign_ids = _email_campaign_ids(first_email)
        for campaign_id in dict.fromkeys(campaign_ids):
            if campaign_id != first_email_id:
                candidate_paths.append(f"/email/public/v1/campaigns/{campaign_id}")
                candidate_paths.append(f"/email/public/v1/campaigns/{campaign_id}/statistics")

        for path in candidate_paths:
            ok, response = client.try_request("GET", path)
            if ok and isinstance(response, dict):
                keys = ", ".join(sorted(response.keys())[:20])
                counters = response.get("counters")
                if isinstance(counters, dict):
                    counter_preview = ", ".join(f"{key}={value}" for key, value in sorted(counters.items())[:20])
                    detail = f"Endpoint accessible. Cles vues: {keys}. Counters: {counter_preview}"
                else:
                    detail = f"Endpoint accessible. Cles vues: {keys}"
                results.append(_diagnostic_result(f"Email stats candidate {path}", True, detail))
            else:
                results.append(_diagnostic_result(f"Email stats candidate {path}", False, str(response)))
    else:
        results.append(_diagnostic_result("Marketing email statistics", False, "Aucun email marketing trouve pour tester les stats."))

    return results


def diagnose_kpi_schema(config: dict[str, Any]) -> list[dict[str, Any]]:
    settings = load_settings()
    client = hubspot_client()
    results: list[dict[str, Any]] = []

    object_type = settings.hubspot_kpi_object_type or config.get("kpi_snapshot", {}).get("object_type")
    if object_type:
        ok, response = client.try_request("GET", f"/crm/v3/schemas/{object_type}")
        if ok and isinstance(response, dict):
            labels = response.get("labels", {})
            properties = response.get("properties", [])
            results.append(
                _diagnostic_result(
                    "KPI Snapshot schema by object type",
                    True,
                    f"Objet trouve: {response.get('objectTypeId') or object_type}, label={labels.get('singular')}, proprietes={len(properties)}.",
                )
            )
        else:
            results.append(_diagnostic_result("KPI Snapshot schema by object type", False, str(response)))

    ok, response = client.try_request("GET", "/crm/v3/schemas")
    if ok and isinstance(response, dict):
        schemas = response.get("results", [])
        matches = [
            schema
            for schema in schemas
            if schema.get("name") == "kpi_snapshot"
            or schema.get("labels", {}).get("singular") == "KPI Snapshot"
        ]
        if matches:
            schema = matches[0]
            results.append(
                _diagnostic_result(
                    "KPI Snapshot schema search",
                    True,
                    f"Objet trouve: objectTypeId={schema.get('objectTypeId')}, name={schema.get('name')}. Utiliser HUBSPOT_KPI_OBJECT_TYPE={schema.get('objectTypeId')}.",
                )
            )
        else:
            results.append(_diagnostic_result("KPI Snapshot schema search", False, f"Aucun objet KPI Snapshot trouve parmi {len(schemas)} schema(s)."))
    else:
        results.append(_diagnostic_result("KPI Snapshot schema search", False, str(response)))

    return results


def create_kpi_schema() -> dict[str, Any]:
    client = hubspot_client()
    schema_client = HubSpotSchemaClient(client)
    return schema_client.create_schema(kpi_snapshot_schema_payload())


def save_and_maybe_sync(snapshots: list[KpiSnapshot], sync_hubspot: bool) -> None:
    settings = load_settings()
    connection = connect(settings.database_path)
    init_db(connection)
    saved = upsert_snapshots(connection, snapshots)
    print(f"{saved} snapshots sauvegardes dans {settings.database_path}")

    if sync_hubspot:
        object_type = settings.hubspot_kpi_object_type
        if not object_type:
            raise RuntimeError("HUBSPOT_KPI_OBJECT_TYPE is required for --sync-hubspot.")
        client = hubspot_client()
        for snapshot in snapshots:
            object_id = upsert_snapshot_to_hubspot(client, object_type, snapshot)
            print(f"HubSpot KPI {snapshot.kpi_code} -> {object_id}")


def export_static_pages(output_dir: Path, weeks: int) -> list[dict[str, Any]]:
    settings = load_settings()
    connection = connect(settings.database_path)
    init_db(connection)
    output_dir.mkdir(parents=True, exist_ok=True)

    mapping = {
        "/newsletter/sent": "newsletter-sent.html",
        "/newsletter/subscribers": "newsletter-subscribers.html",
        "/newsletter/open-rate": "newsletter-open-rate.html",
        "/website/visits": "website-visits.html",
        "/linkedin/posts": "linkedin-posts.html",
        "/linkedin/engagement-rate": "linkedin-engagement-rate.html",
    }
    exported = []
    cards = []
    for path, filename in mapping.items():
        chart = CHARTS[path]
        if chart.get("series"):
            rows = latest_snapshots_for_kpi_series(connection, chart["kpi_code"], limit=weeks, segment=chart["segment"])
        else:
            rows = latest_snapshots_for_kpi(connection, chart["kpi_code"], limit=weeks, segment=chart["segment"])
        related_rows = {}
        if chart.get("secondary_kpi"):
            secondary = chart["secondary_kpi"]
            secondary_rows = latest_snapshots_for_kpi(connection, secondary["kpi_code"], limit=weeks, segment=chart["segment"])
            related_rows[secondary["kpi_code"]] = rows_as_dicts(secondary_rows)
        html = render_page(path, rows_as_dicts(rows), related_rows)
        (output_dir / filename).write_text(html, encoding="utf-8")
        exported.append({"file": filename, "title": chart["title"], "points": len(rows)})
        cards.append((chart["title"], filename, len(rows)))

    index_cards = "\n".join(
        f'<a class="card" href="{filename}"><span>{title}</span><small>{count} point(s)</small></a>'
        for title, filename, count in cards
    )
    index = f"""<!doctype html>
<html lang="fr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>KPI Dashboard - Le Cercle MDB</title>
  <style>
    :root {{ color-scheme: dark; font-family: Inter, Arial, sans-serif; }}
    body {{ margin: 0; min-height: 100vh; background: #101223; color: #F8FAFC; }}
    main {{ max-width: 920px; margin: 0 auto; padding: 34px 24px; }}
    h1 {{ margin: 0 0 8px; font-size: 30px; }}
    p {{ margin: 0 0 24px; color: #A7ADC3; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(230px, 1fr)); gap: 14px; }}
    .card {{ display: flex; flex-direction: column; gap: 10px; padding: 18px; border: 1px solid #252A48; border-radius: 8px; background: #151832; color: #F8FAFC; text-decoration: none; }}
    .card:hover {{ border-color: #C9A24A; }}
    span {{ font-size: 18px; font-weight: 750; }}
    small {{ color: #C9A24A; }}
  </style>
</head>
<body>
  <main>
    <h1>KPI Dashboard</h1>
    <p>Pages statiques pretes pour publication GitHub Pages et integration HubSpot.</p>
    <div class="grid">
      {index_cards}
    </div>
  </main>
</body>
</html>
"""
    (output_dir / "index.html").write_text(index, encoding="utf-8")
    exported.insert(0, {"file": "index.html", "title": "KPI Dashboard", "points": None})
    return exported


def print_snapshots(snapshots: list[KpiSnapshot]) -> None:
    payload = [
        {
            "kpi_code": item.kpi_code,
            "period_type": item.period_type,
            "period_start": item.period_start.isoformat(),
            "period_end": item.period_end.isoformat(),
            "value": item.value,
            "unit": item.unit,
            "segment": item.segment,
            "dimension_1": item.dimension_1,
            "dimension_2": item.dimension_2,
        }
        for item in snapshots
    ]
    print(json.dumps(payload, indent=2, ensure_ascii=False))


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    settings = load_settings()
    config = load_config(settings.config_path)

    if args.command == "init-db":
        connection = connect(settings.database_path)
        init_db(connection)
        print(f"Base initialisee: {settings.database_path}")
        return

    if args.command == "compact-db":
        connection = connect(settings.database_path)
        init_db(connection)
        removed = compact_snapshot_ids(connection)
        print(f"{removed} doublon(s) supprime(s) dans {settings.database_path}")
        return

    if args.command == "diagnose-newsletter":
        try:
            print(json.dumps(diagnose_newsletter(config), indent=2, ensure_ascii=False))
        except RuntimeError as error:
            print(
                json.dumps(
                    [
                        {
                            "check": "HubSpot token",
                            "ok": False,
                            "detail": str(error),
                            "next_step": "Renseigner HUBSPOT_ACCESS_TOKEN puis relancer diagnose-newsletter.",
                        }
                    ],
                    indent=2,
                    ensure_ascii=False,
                )
            )
        return

    if args.command == "diagnose-ga4":
        try:
            print(json.dumps(diagnose_ga4(config), indent=2, ensure_ascii=False))
        except RuntimeError as error:
            print(
                json.dumps(
                    [
                        {
                            "check": "GA4 access",
                            "ok": False,
                            "detail": str(error),
                            "next_step": "Renseigner GA4_PROPERTY_ID et GOOGLE_APPLICATION_CREDENTIALS, puis donner au compte de service un acces lecteur a la propriete GA4.",
                        }
                    ],
                    indent=2,
                    ensure_ascii=False,
                )
            )
        return

    if args.command == "diagnose-metricool":
        try:
            print(json.dumps(diagnose_metricool(config), indent=2, ensure_ascii=False))
        except RuntimeError as error:
            print(
                json.dumps(
                    [
                        {
                            "check": "Metricool access",
                            "ok": False,
                            "detail": str(error),
                            "next_step": "Renseigner METRICOOL_USER_TOKEN puis configurer user_id/blog_id pour les 3 comptes LinkedIn.",
                        }
                    ],
                    indent=2,
                    ensure_ascii=False,
                )
            )
        return

    if args.command == "diagnose-ga4-hostnames":
        try:
            print(json.dumps(diagnose_ga4_hostnames(args.days), indent=2, ensure_ascii=False))
        except RuntimeError as error:
            print(
                json.dumps(
                    [
                        {
                            "check": "GA4 hostnames",
                            "ok": False,
                            "detail": str(error),
                            "next_step": "Verifier GA4_PROPERTY_ID, GOOGLE_APPLICATION_CREDENTIALS et l'acces lecteur du compte de service.",
                        }
                    ],
                    indent=2,
                    ensure_ascii=False,
                )
            )
        return

    if args.command == "diagnose-kpi-schema":
        try:
            print(json.dumps(diagnose_kpi_schema(config), indent=2, ensure_ascii=False))
        except RuntimeError as error:
            print(
                json.dumps(
                    [
                        {
                            "check": "HubSpot schema access",
                            "ok": False,
                            "detail": str(error),
                            "next_step": "Ajouter les scopes de schemas CRM/custom objects ou creer l'objet manuellement dans HubSpot.",
                        }
                    ],
                    indent=2,
                    ensure_ascii=False,
                )
            )
        return

    if args.command == "create-kpi-schema":
        try:
            schema = create_kpi_schema()
            print(
                json.dumps(
                    {
                        "ok": True,
                        "objectTypeId": schema.get("objectTypeId"),
                        "name": schema.get("name"),
                        "next_step": f"Relancer avec HUBSPOT_KPI_OBJECT_TYPE={schema.get('objectTypeId')}",
                    },
                    indent=2,
                    ensure_ascii=False,
                )
            )
        except RuntimeError as error:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "detail": str(error),
                        "next_step": "Si l'API refuse la creation, creer l'objet custom KPI Snapshot manuellement avec les proprietes du README.",
                    },
                    indent=2,
                    ensure_ascii=False,
                )
            )
        return

    if args.command == "backfill-newsletter":
        snapshots = compute_newsletter_backfill(date.fromisoformat(args.date), args.weeks, config, dry_run=args.dry_run)
        print_snapshots(snapshots)
        if args.save:
            save_and_maybe_sync(snapshots, sync_hubspot=False)
        return

    if args.command == "import-website-visits":
        snapshots = read_website_visits_csv(Path(args.csv))
        print_snapshots(snapshots)
        if args.save:
            save_and_maybe_sync(snapshots, sync_hubspot=False)
        return

    if args.command == "backfill-website":
        snapshots = compute_website_backfill(date.fromisoformat(args.date), args.weeks, config)
        print_snapshots(snapshots)
        if args.save:
            save_and_maybe_sync(snapshots, sync_hubspot=False)
        return

    if args.command == "backfill-linkedin":
        snapshots = compute_linkedin_backfill(date.fromisoformat(args.date), args.weeks, config)
        print_snapshots(snapshots)
        if args.save:
            save_and_maybe_sync(snapshots, sync_hubspot=False)
        return

    if args.command == "export-static":
        exported = export_static_pages(Path(args.output), args.weeks)
        print(json.dumps(exported, indent=2, ensure_ascii=False))
        return

    period = selected_period(args.period, args.date)
    snapshots: list[KpiSnapshot] = []
    if args.command in {"run-hubspot", "run-all"}:
        snapshots.extend(compute_hubspot_snapshots(period, config, args.dry_run))
    if args.command in {"run-newsletter", "run-all"}:
        snapshots.extend(compute_newsletter_snapshots(period, config, args.dry_run))

    print_snapshots(snapshots)
    if args.save or args.sync_hubspot:
        save_and_maybe_sync(snapshots, args.sync_hubspot)


if __name__ == "__main__":
    main()
