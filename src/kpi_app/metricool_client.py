from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


METRICOOL_BASE_URL = "https://app.metricool.com/api"


@dataclass
class MetricoolClient:
    user_token: str
    base_url: str = METRICOOL_BASE_URL
    timeout_seconds: int = 30

    def request(self, path: str, query: dict[str, Any] | None = None) -> Any:
        url = f"{self.base_url.rstrip('/')}/{path.lstrip('/')}"
        if query:
            url = f"{url}?{urlencode(query, doseq=True)}"
        request = Request(
            url,
            method="GET",
            headers={
                "X-Mc-Auth": self.user_token,
                "Accept": "application/json",
            },
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                raw = response.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except HTTPError as error:
            detail = error.read().decode("utf-8")
            raise RuntimeError(f"Metricool API error {error.code} for GET {path}: {detail}") from error
        except TimeoutError as error:
            raise RuntimeError(f"Metricool API timeout apres {self.timeout_seconds}s.") from error
        except URLError as error:
            raise RuntimeError(f"Metricool API network error: {error.reason}") from error

    def linkedin_posts(
        self,
        user_id: str,
        blog_id: str,
        start_date: str,
        end_date: str,
        timezone: str,
    ) -> list[dict[str, Any]]:
        payload = self.request(
            "/stats/linkedin/posts",
            query={
                "userId": user_id,
                "blogId": blog_id,
                "from": start_date,
                "to": end_date,
                "timezone": timezone,
            },
        )
        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict) and isinstance(payload.get("data"), list):
            return payload["data"]
        if isinstance(payload, dict) and isinstance(payload.get("posts"), list):
            return payload["posts"]
        return []
