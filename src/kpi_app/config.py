from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Settings:
    hubspot_access_token: str | None
    hubspot_base_url: str
    database_path: Path
    config_path: Path
    hubspot_kpi_object_type: str | None
    ga4_property_id: str | None
    google_application_credentials: Path | None
    ga4_timeout_seconds: int
    metricool_user_token: str | None
    metricool_base_url: str
    metricool_timezone: str
    metricool_timeout_seconds: int


def load_settings() -> Settings:
    config_path = Path(os.environ.get("KPI_CONFIG_PATH", "./config/kpi_config.example.json"))
    return Settings(
        hubspot_access_token=os.environ.get("HUBSPOT_ACCESS_TOKEN"),
        hubspot_base_url=os.environ.get("HUBSPOT_BASE_URL", "https://api.hubapi.com"),
        database_path=Path(os.environ.get("KPI_DATABASE_PATH", "./kpi.sqlite3")),
        config_path=config_path,
        hubspot_kpi_object_type=os.environ.get("HUBSPOT_KPI_OBJECT_TYPE"),
        ga4_property_id=os.environ.get("GA4_PROPERTY_ID"),
        google_application_credentials=Path(os.environ["GOOGLE_APPLICATION_CREDENTIALS"])
        if os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
        else None,
        ga4_timeout_seconds=int(os.environ.get("GA4_TIMEOUT_SECONDS", "15")),
        metricool_user_token=os.environ.get("METRICOOL_USER_TOKEN"),
        metricool_base_url=os.environ.get("METRICOOL_BASE_URL", "https://app.metricool.com/api"),
        metricool_timezone=os.environ.get("METRICOOL_TIMEZONE", "Europe/Paris"),
        metricool_timeout_seconds=int(os.environ.get("METRICOOL_TIMEOUT_SECONDS", "30")),
    )


def load_config(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def require_token(settings: Settings) -> str:
    if not settings.hubspot_access_token:
        raise RuntimeError("HUBSPOT_ACCESS_TOKEN is required for HubSpot API calls.")
    return settings.hubspot_access_token


def require_ga4_property_id(settings: Settings) -> str:
    if not settings.ga4_property_id:
        raise RuntimeError("GA4_PROPERTY_ID is required for GA4 API calls.")
    return settings.ga4_property_id


def require_google_credentials(settings: Settings) -> Path:
    if not settings.google_application_credentials:
        raise RuntimeError("GOOGLE_APPLICATION_CREDENTIALS is required for GA4 API calls.")
    return settings.google_application_credentials


def require_metricool_user_token(settings: Settings) -> str:
    if not settings.metricool_user_token:
        raise RuntimeError("METRICOOL_USER_TOKEN is required for Metricool API calls.")
    return settings.metricool_user_token
