from __future__ import annotations

import base64
import json
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


GA4_SCOPE = "https://www.googleapis.com/auth/analytics.readonly"
TOKEN_URL = "https://oauth2.googleapis.com/token"
GA4_BASE_URL = "https://analyticsdata.googleapis.com"


def _base64url(payload: bytes) -> str:
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _sign_rs256(message: bytes, private_key: str) -> bytes:
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=True) as key_file:
        key_file.write(private_key)
        key_file.flush()
        result = subprocess.run(
            ["openssl", "dgst", "-sha256", "-sign", key_file.name],
            input=message,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    if result.returncode != 0:
        raise RuntimeError(f"OpenSSL signing failed: {result.stderr.decode('utf-8', errors='ignore')}")
    return result.stdout


def _service_account_assertion(credentials: dict[str, Any]) -> str:
    now = int(time.time())
    header = {"alg": "RS256", "typ": "JWT"}
    payload = {
        "iss": credentials["client_email"],
        "scope": GA4_SCOPE,
        "aud": credentials.get("token_uri") or TOKEN_URL,
        "iat": now,
        "exp": now + 3600,
    }
    encoded_header = _base64url(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    encoded_payload = _base64url(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signing_input = f"{encoded_header}.{encoded_payload}".encode("ascii")
    signature = _base64url(_sign_rs256(signing_input, credentials["private_key"]))
    return f"{encoded_header}.{encoded_payload}.{signature}"


@dataclass
class Ga4Client:
    credentials_path: Path
    base_url: str = GA4_BASE_URL
    timeout_seconds: int = 30
    _access_token: str | None = None

    def access_token(self) -> str:
        if self._access_token:
            return self._access_token
        credentials = json.loads(self.credentials_path.read_text(encoding="utf-8"))
        assertion = _service_account_assertion(credentials)
        body = urlencode(
            {
                "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                "assertion": assertion,
            }
        ).encode("utf-8")
        request = Request(
            credentials.get("token_uri") or TOKEN_URL,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            detail = error.read().decode("utf-8")
            raise RuntimeError(f"Google OAuth error {error.code}: {detail}") from error
        except TimeoutError as error:
            raise RuntimeError(f"Google OAuth timeout apres {self.timeout_seconds}s.") from error
        except URLError as error:
            raise RuntimeError(f"Google OAuth network error: {error.reason}") from error
        self._access_token = payload["access_token"]
        return self._access_token

    def run_report(self, property_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.base_url.rstrip('/')}/v1beta/properties/{property_id}:runReport"
        request = Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers={
                "Authorization": f"Bearer {self.access_token()}",
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
            raise RuntimeError(f"GA4 API error {error.code}: {detail}") from error
        except TimeoutError as error:
            raise RuntimeError(f"GA4 API timeout apres {self.timeout_seconds}s.") from error
        except URLError as error:
            raise RuntimeError(f"GA4 API network error: {error.reason}") from error


def hostname_filter(hostnames: list[str]) -> dict[str, Any] | None:
    cleaned = [hostname.strip() for hostname in hostnames if hostname.strip()]
    if not cleaned:
        return None
    return {
        "filter": {
            "fieldName": "hostName",
            "inListFilter": {
                "values": cleaned,
                "caseSensitive": False,
            },
        }
    }
