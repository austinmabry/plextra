"""Small shared HTTP helper for providers."""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from .base import ProviderAuthError, ProviderError

log = logging.getLogger(__name__)

USER_AGENT = "Plextra/0.1 (+https://github.com/austinmabry/plextra)"


class HttpMixin:
    """Retry, rate-limit and error handling shared by every HTTP provider."""

    timeout: float = 30.0
    _client: httpx.Client | None = None

    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                timeout=self.timeout,
                headers={"User-Agent": USER_AGENT},
                follow_redirects=True,
            )
        return self._client

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        json_body: Any = None,
        max_attempts: int = 3,
        expect_json: bool = True,
    ) -> Any:
        last_error = ""

        for attempt in range(1, max_attempts + 1):
            try:
                response = self.client.request(
                    method, url, params=params, headers=headers, json=json_body
                )
            except httpx.HTTPError as exc:
                last_error = str(exc)
                log.warning("%s: request to %s failed: %s", self.name, url, exc)
            else:
                if response.status_code in (401, 403):
                    raise ProviderAuthError(
                        f"{self.name} rejected the credentials "
                        f"(HTTP {response.status_code}). Check them in Settings."
                    )
                if response.status_code == 404:
                    raise ProviderError(f"{self.name} could not find that list (HTTP 404).")
                if response.status_code == 429:
                    wait = float(response.headers.get("Retry-After", 2 * attempt))
                    log.warning("%s rate limited us; waiting %.0fs.", self.name, wait)
                    time.sleep(min(wait, 60))
                    continue
                if response.status_code >= 500:
                    last_error = f"HTTP {response.status_code}"
                elif response.status_code >= 400:
                    raise ProviderError(
                        f"{self.name} returned HTTP {response.status_code} for {url}."
                    )
                else:
                    if not expect_json:
                        return response.text
                    try:
                        return response.json()
                    except ValueError as exc:
                        raise ProviderError(
                            f"{self.name} sent a non-JSON reply for {url}."
                        ) from exc

            if attempt < max_attempts:
                time.sleep(2**attempt)

        raise ProviderError(f"{self.name} request failed after {max_attempts} tries: {last_error}")

    def get_json(self, url: str, **kwargs: Any) -> Any:
        return self.request("GET", url, **kwargs)

    def get_text(self, url: str, **kwargs: Any) -> str:
        return self.request("GET", url, expect_json=False, **kwargs)
