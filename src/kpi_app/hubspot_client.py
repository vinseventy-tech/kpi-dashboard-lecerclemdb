from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


@dataclass
class HubSpotClient:
    access_token: str
    base_url: str = "https://api.hubapi.com"
    timeout_seconds: int = 30

    def request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        query: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = f"{self.base_url.rstrip('/')}/{path.lstrip('/')}"
        if query:
            url = f"{url}?{urlencode(query, doseq=True)}"

        body = None
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")

        request = Request(
            url,
            data=body,
            method=method.upper(),
            headers={
                "Authorization": f"Bearer {self.access_token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )

        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                raw = response.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except HTTPError as error:
            detail = error.read().decode("utf-8")
            raise RuntimeError(f"HubSpot API error {error.code} for {method} {path}: {detail}") from error

    def paged_get(self, path: str, query: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        query = dict(query or {})
        results: list[dict[str, Any]] = []
        after = None
        while True:
            current_query = dict(query)
            if after:
                current_query["after"] = after
            response = self.request("GET", path, query=current_query)
            results.extend(response.get("results", []))
            after = response.get("paging", {}).get("next", {}).get("after")
            if not after:
                return results
            time.sleep(0.15)

    def try_request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        query: dict[str, Any] | None = None,
    ) -> tuple[bool, dict[str, Any] | str]:
        try:
            return True, self.request(method, path, payload=payload, query=query)
        except RuntimeError as error:
            return False, str(error)

    def search_objects(
        self,
        object_type: str,
        properties: list[str],
        filters: list[dict[str, Any]] | None = None,
        sorts: list[str] | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        after: str | None = None
        while True:
            payload: dict[str, Any] = {
                "limit": limit,
                "properties": properties,
            }
            if filters:
                payload["filterGroups"] = [{"filters": filters}]
            if sorts:
                payload["sorts"] = sorts
            if after:
                payload["after"] = after

            response = self.request("POST", f"/crm/v3/objects/{object_type}/search", payload=payload)
            results.extend(response.get("results", []))
            after = response.get("paging", {}).get("next", {}).get("after")
            if not after:
                return results
            time.sleep(0.15)

    def list_associations(self, from_object: str, object_id: str, to_object: str) -> list[dict[str, Any]]:
        path = f"/crm/v4/objects/{from_object}/{object_id}/associations/{to_object}"
        return self.paged_get(path)

    def create_object(self, object_type: str, properties: dict[str, Any]) -> dict[str, Any]:
        return self.request("POST", f"/crm/v3/objects/{object_type}", payload={"properties": properties})

    def update_object(self, object_type: str, object_id: str, properties: dict[str, Any]) -> dict[str, Any]:
        return self.request("PATCH", f"/crm/v3/objects/{object_type}/{object_id}", payload={"properties": properties})


@dataclass
class HubSpotMarketingEmailClient:
    client: HubSpotClient

    def list_emails(self) -> list[dict[str, Any]]:
        return self.client.paged_get("/marketing/v3/emails", query={"limit": 100})

    def list_emails_page(self, limit: int = 25, after: str | None = None) -> dict[str, Any]:
        query: dict[str, Any] = {"limit": limit}
        if after:
            query["after"] = after
        return self.client.request("GET", "/marketing/v3/emails", query=query)

    def get_email_statistics(self, email_id: str) -> dict[str, Any]:
        # HubSpot accounts/scopes can expose marketing email statistics differently.
        # Keeping this in one method makes the connector easy to adapt after scope validation.
        return self.client.request("GET", f"/marketing/v3/emails/{email_id}/statistics")

    def get_legacy_campaign(self, campaign_id: str) -> dict[str, Any]:
        return self.client.request("GET", f"/email/public/v1/campaigns/{campaign_id}")


@dataclass
class HubSpotListClient:
    client: HubSpotClient

    def list_segment_memberships(self, list_id: str) -> list[dict[str, Any]]:
        # HubSpot's current lists API is exposed under CRM lists. The app keeps this
        # isolated because some portals still require the legacy list ID as fallback.
        return self.client.paged_get(f"/crm/v3/lists/{list_id}/memberships", query={"limit": 100})

    def count_segment_memberships(self, list_id: str) -> int:
        return len(self.list_segment_memberships(list_id))

    def legacy_list_contacts(self, legacy_list_id: str) -> list[dict[str, Any]]:
        contacts: list[dict[str, Any]] = []
        vid_offset: int | None = None
        while True:
            query: dict[str, Any] = {"count": 100}
            if vid_offset is not None:
                query["vidOffset"] = vid_offset
            response = self.client.request(
                "GET",
                f"/contacts/v1/lists/{legacy_list_id}/contacts/all",
                query=query,
            )
            contacts.extend(response.get("contacts", []))
            if not response.get("has-more"):
                return contacts
            vid_offset = response.get("vid-offset")
            time.sleep(0.15)

    def count_legacy_list_contacts(self, legacy_list_id: str) -> int:
        return len(self.legacy_list_contacts(legacy_list_id))


@dataclass
class HubSpotSchemaClient:
    client: HubSpotClient

    def list_schemas(self) -> list[dict[str, Any]]:
        return self.client.paged_get("/crm/v3/schemas")

    def get_schema(self, object_type: str) -> dict[str, Any]:
        return self.client.request("GET", f"/crm/v3/schemas/{object_type}")

    def create_schema(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.client.request("POST", "/crm/v3/schemas", payload=payload)
