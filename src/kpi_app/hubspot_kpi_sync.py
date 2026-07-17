from __future__ import annotations

from .hubspot_client import HubSpotClient
from .models import KpiSnapshot
from .storage import snapshot_id


def kpi_snapshot_schema_payload() -> dict:
    return {
        "name": "kpi_snapshot",
        "labels": {
            "singular": "KPI Snapshot",
            "plural": "KPI Snapshots",
        },
        "primaryDisplayProperty": "kpi_name",
        "requiredProperties": ["kpi_external_id", "kpi_code", "period_start"],
        "searchableProperties": ["kpi_external_id", "kpi_code", "kpi_name", "segment", "owner"],
        "secondaryDisplayProperties": ["period_start", "period_type", "value", "unit"],
        "properties": [
            {
                "name": "kpi_external_id",
                "label": "KPI External ID",
                "type": "string",
                "fieldType": "text",
                "description": "Stable technical ID used by the KPI app to upsert snapshots.",
                "hasUniqueValue": True,
            },
            {"name": "kpi_code", "label": "KPI Code", "type": "string", "fieldType": "text"},
            {"name": "kpi_name", "label": "KPI Name", "type": "string", "fieldType": "text"},
            {
                "name": "period_type",
                "label": "Period Type",
                "type": "enumeration",
                "fieldType": "select",
                "options": [
                    {"label": "Week", "value": "week", "displayOrder": 0, "hidden": False},
                    {"label": "Month", "value": "month", "displayOrder": 1, "hidden": False},
                ],
            },
            {"name": "period_start", "label": "Period Start", "type": "date", "fieldType": "date"},
            {"name": "period_end", "label": "Period End", "type": "date", "fieldType": "date"},
            {"name": "value", "label": "Value", "type": "number", "fieldType": "number"},
            {
                "name": "unit",
                "label": "Unit",
                "type": "enumeration",
                "fieldType": "select",
                "options": [
                    {"label": "Count", "value": "count", "displayOrder": 0, "hidden": False},
                    {"label": "Percent", "value": "percent", "displayOrder": 1, "hidden": False},
                    {"label": "Seconds", "value": "seconds", "displayOrder": 2, "hidden": False},
                    {"label": "Minutes", "value": "minutes", "displayOrder": 3, "hidden": False},
                    {"label": "Ratio", "value": "ratio", "displayOrder": 4, "hidden": False},
                ],
            },
            {
                "name": "source",
                "label": "Source",
                "type": "enumeration",
                "fieldType": "select",
                "options": [
                    {"label": "HubSpot", "value": "hubspot", "displayOrder": 0, "hidden": False},
                    {"label": "GA4", "value": "ga4", "displayOrder": 1, "hidden": False},
                    {"label": "YouTube", "value": "youtube", "displayOrder": 2, "hidden": False},
                    {"label": "Metricool", "value": "metricool", "displayOrder": 3, "hidden": False},
                    {"label": "Sheets", "value": "sheets", "displayOrder": 4, "hidden": False},
                ],
            },
            {"name": "segment", "label": "Segment", "type": "string", "fieldType": "text"},
            {"name": "owner", "label": "Owner", "type": "string", "fieldType": "text"},
            {"name": "dimension_1", "label": "Dimension 1", "type": "string", "fieldType": "text"},
            {"name": "dimension_2", "label": "Dimension 2", "type": "string", "fieldType": "text"},
            {"name": "source_record_count", "label": "Source Record Count", "type": "number", "fieldType": "number"},
            {"name": "dedupe_method", "label": "Dedupe Method", "type": "string", "fieldType": "text"},
            {"name": "computed_at", "label": "Computed At", "type": "datetime", "fieldType": "date"},
        ],
    }


def snapshot_properties(snapshot: KpiSnapshot) -> dict[str, str | float | int | None]:
    item = snapshot.with_computed_at()
    return {
        "kpi_external_id": snapshot_id(item),
        "kpi_code": item.kpi_code,
        "kpi_name": item.kpi_name,
        "period_type": item.period_type,
        "period_start": item.period_start.isoformat(),
        "period_end": item.period_end.isoformat(),
        "value": item.value,
        "unit": item.unit,
        "source": item.source,
        "segment": item.segment,
        "owner": item.owner,
        "dimension_1": item.dimension_1,
        "dimension_2": item.dimension_2,
        "source_record_count": item.source_record_count,
        "dedupe_method": item.dedupe_method,
        "computed_at": item.computed_at.isoformat(),
    }


def upsert_snapshot_to_hubspot(
    client: HubSpotClient,
    object_type: str,
    snapshot: KpiSnapshot,
) -> str:
    external_id = snapshot_id(snapshot)
    matches = client.search_objects(
        object_type,
        properties=["kpi_external_id"],
        filters=[
            {
                "propertyName": "kpi_external_id",
                "operator": "EQ",
                "value": external_id,
            }
        ],
        limit=1,
    )
    properties = snapshot_properties(snapshot)
    if matches:
        object_id = matches[0]["id"]
        client.update_object(object_type, object_id, properties)
        return object_id
    created = client.create_object(object_type, properties)
    return created["id"]
