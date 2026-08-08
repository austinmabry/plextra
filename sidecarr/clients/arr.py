"""Shared client for the Radarr/Sonarr v3 API surface."""

from __future__ import annotations

import logging
import time
from typing import Any
from urllib.parse import urljoin

import httpx

log = logging.getLogger(__name__)


class ArrError(Exception):
    """Any failure talking to Radarr or Sonarr."""


class ArrMetadataError(ArrError):
    """Radarr/Sonarr's own metadata service failed.

    Distinct from a title being unknown. Radarr proxies metadata through
    api.radarr.video, which answers a genuine miss with a clean 404; a 5xx means
    that service, or the network to it, failed. Those are transient, so the
    title is worth retrying rather than writing off.
    """


class ArrUnknownIdError(ArrError):
    """Radarr/Sonarr's metadata service does not recognise the ID at all."""


class ArrClient:
    """Common behaviour for Radarr and Sonarr.

    Both expose ``/api/v3`` and authenticate with an ``X-Api-Key`` header, so
    everything except the media-specific endpoints lives here.
    """

    service = "arr"

    def __init__(
        self, url: str, api_key: str, timeout: float = 60.0, retries: int = 3
    ) -> None:
        if not url:
            raise ArrError(f"No {self.service} URL configured.")
        if not api_key:
            raise ArrError(f"No {self.service} API key configured.")
        self.base_url = url.strip().rstrip("/") + "/"
        self.api_key = api_key.strip()
        # Background syncs want retries; an interactive "Test" button wants a
        # fast answer, so it constructs the client with retries=1.
        self.retries = max(1, retries)
        self._client = httpx.Client(
            timeout=timeout,
            headers={"X-Api-Key": self.api_key, "Content-Type": "application/json"},
            follow_redirects=False,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "ArrClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- requests ----------------------------------------------------------- #

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: Any = None,
        max_attempts: int | None = None,
        timeout: float | None = None,
    ) -> httpx.Response:
        url = urljoin(self.base_url, path.lstrip("/"))
        max_attempts = self.retries if max_attempts is None else max_attempts
        last_error = ""
        server_error = False

        for attempt in range(1, max_attempts + 1):
            try:
                response = self._client.request(
                    method,
                    url,
                    params=params,
                    json=json_body,
                    **({"timeout": timeout} if timeout is not None else {}),
                )
            except httpx.HTTPError as exc:
                last_error = str(exc)
                log.warning("%s request to %s failed: %s", self.service, path, exc)
            else:
                if response.status_code == 401:
                    raise ArrError(f"{self.service} rejected the API key.")
                if response.status_code < 500:
                    return response
                last_error = f"HTTP {response.status_code}"
                server_error = True
                log.warning("%s returned %s for %s.", self.service, response.status_code, path)

            if attempt < max_attempts:
                time.sleep(2**attempt)

        if server_error:
            # Name the ID that was asked for. Without it the message is
            # untestable, and the first thing worth knowing is whether the
            # metadata service can serve that ID at all.
            asked_for = ""
            for key in ("tmdbId", "tvdbId", "term"):
                if params and params.get(key):
                    asked_for = f" (for {key}={params[key]})"
                    break
            raise ArrMetadataError(
                f"{self.service} returned {last_error} for {path}{asked_for} after "
                f"{max_attempts} tries. {self.service} answers a title it genuinely "
                "does not know with a 404, so a 500 means its metadata service "
                f"failed or {self.service} could not reach it."
            )
        raise ArrError(f"{self.service} request to {path} failed: {last_error}")

    def get_json(self, path: str, params: dict[str, Any] | None = None) -> Any:
        response = self.request("GET", path, params=params)
        if response.status_code != 200:
            raise ArrError(
                f"{self.service} returned HTTP {response.status_code} for {path}."
            )
        try:
            return response.json()
        except ValueError as exc:
            raise ArrError(
                f"{self.service} sent a non-JSON reply for {path}. Is the URL pointing "
                "at the app and not a reverse proxy error page?"
            ) from exc

    # -- shared endpoints --------------------------------------------------- #

    def system_status(self) -> dict[str, Any]:
        return self.get_json("api/v3/system/status")

    def version(self) -> str:
        return str(self.system_status().get("version", "unknown"))

    def quality_profiles(self) -> list[dict[str, Any]]:
        return self.get_json("api/v3/qualityprofile")

    def root_folders(self) -> list[dict[str, Any]]:
        return self.get_json("api/v3/rootfolder")

    def tags(self) -> list[dict[str, Any]]:
        return self.get_json("api/v3/tag")

    def create_tag(self, label: str) -> dict[str, Any]:
        response = self.request("POST", "api/v3/tag", json_body={"label": label})
        if response.status_code not in (200, 201):
            raise ArrError(f"Could not create the tag {label!r} in {self.service}.")
        return response.json()

    def test(self) -> dict[str, Any]:
        """Everything the settings screen needs in one round trip set."""
        status = self.system_status()
        return {
            "ok": True,
            "version": status.get("version", "unknown"),
            "app_name": status.get("appName", self.service),
            "quality_profiles": [
                {"id": p["id"], "name": p["name"]} for p in self.quality_profiles()
            ],
            "root_folders": [
                {"id": f.get("id"), "path": f.get("path"), "free_space": f.get("freeSpace")}
                for f in self.root_folders()
            ],
            "tags": [{"id": t["id"], "label": t["label"]} for t in self.tags()],
        }

    # -- helpers ------------------------------------------------------------ #

    @staticmethod
    def error_message(response: httpx.Response) -> str:
        """Pull the human-readable reason out of an *arr error response."""
        try:
            payload = response.json()
        except ValueError:
            return (response.text or "").strip()[:300] or f"HTTP {response.status_code}"

        if isinstance(payload, list):
            messages = []
            for entry in payload:
                if isinstance(entry, dict):
                    messages.append(
                        entry.get("errorMessage") or entry.get("message") or str(entry)
                    )
            if messages:
                return "; ".join(messages)[:300]
        elif isinstance(payload, dict):
            message = payload.get("errorMessage") or payload.get("message")
            if message:
                return str(message)[:300]
        return f"HTTP {response.status_code}"
