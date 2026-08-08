"""Trakt API client: OAuth device flow, list fetching, pagination."""

from __future__ import annotations

import logging
import re
import time
from typing import Any, Callable, Iterable

import httpx

from ..config import Source, TraktAccount, TraktConfig
from ..settings import MAX_TRAKT_PAGES

log = logging.getLogger(__name__)

BASE = "https://api.trakt.tv"
PAGE_SIZE = 100

# Refresh a token once it is within this many seconds of expiring.
REFRESH_WINDOW = 24 * 60 * 60


class TraktError(Exception):
    """Any failure talking to Trakt."""


class TraktAuthError(TraktError):
    """The stored token is missing, rejected or unrecoverable."""


def parse_list_url(value: str) -> tuple[str, str]:
    """Accept a Trakt list URL or ``user/list-slug`` and split it apart."""
    value = (value or "").strip()
    if not value:
        raise TraktError("No Trakt list URL was given.")

    match = re.search(r"/users/([^/]+)/lists/([^/?#]+)", value)
    if match:
        return match.group(1), match.group(2)

    parts = [p for p in value.split("/") if p]
    if len(parts) == 2:
        return parts[0], parts[1]

    raise TraktError(
        f"{value!r} is not a Trakt list. Use the list URL "
        "(https://trakt.tv/users/<user>/lists/<list>) or <user>/<list>."
    )


def _slugify_person(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower())
    return slug.strip("-")


class TraktClient:
    """Talks to Trakt on behalf of one configured application + account."""

    def __init__(
        self,
        trakt_cfg: TraktConfig,
        on_account_update: Callable[[str, TraktAccount], None] | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.cfg = trakt_cfg
        self._on_account_update = on_account_update
        self._client = httpx.Client(
            timeout=timeout,
            headers={"User-Agent": "Sidecarr/0.1 (+https://github.com/austinmabry/sidecarr)"},
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "TraktClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- headers ----------------------------------------------------------- #

    def _base_headers(self) -> dict[str, str]:
        if not self.cfg.client_id:
            raise TraktAuthError("No Trakt client ID configured.")
        return {
            "Content-Type": "application/json",
            "trakt-api-version": "2",
            "trakt-api-key": self.cfg.client_id,
        }

    def _headers(self, account: str | None) -> dict[str, str]:
        headers = self._base_headers()
        if account:
            token = self._access_token(account)
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def _access_token(self, account: str) -> str:
        stored = self.cfg.accounts.get(account)
        if not stored or not stored.access_token:
            raise TraktAuthError(
                f"Trakt account {account!r} is not authorised. Connect it in Settings."
            )
        if stored.created_at and stored.expires_in:
            expires_at = stored.created_at + stored.expires_in
            if expires_at - time.time() < REFRESH_WINDOW:
                stored = self._refresh(account, stored)
        return stored.access_token

    def _refresh(self, account: str, stored: TraktAccount) -> TraktAccount:
        if not stored.refresh_token:
            raise TraktAuthError(
                f"The Trakt token for {account!r} expired and there is no refresh "
                "token. Reconnect the account in Settings."
            )
        log.info("Refreshing the Trakt access token for %s.", account)
        response = self._client.post(
            f"{BASE}/oauth/token",
            headers=self._base_headers(),
            json={
                "refresh_token": stored.refresh_token,
                "client_id": self.cfg.client_id,
                "client_secret": self.cfg.client_secret,
                "redirect_uri": "urn:ietf:wg:oauth:2.0:oob",
                "grant_type": "refresh_token",
            },
        )
        if response.status_code != 200:
            raise TraktAuthError(
                f"Trakt refused to refresh the token for {account!r} "
                f"(HTTP {response.status_code}). Reconnect the account in Settings."
            )
        updated = TraktAccount.model_validate(response.json())
        self.cfg.accounts[account] = updated
        if self._on_account_update:
            self._on_account_update(account, updated)
        return updated

    # -- low level requests ------------------------------------------------ #

    def _request(
        self,
        method: str,
        path: str,
        *,
        account: str | None = None,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        max_attempts: int = 4,
    ) -> httpx.Response:
        url = f"{BASE}{path}"
        last_error = ""

        for attempt in range(1, max_attempts + 1):
            try:
                response = self._client.request(
                    method,
                    url,
                    headers=self._headers(account),
                    params=params,
                    json=json_body,
                )
            except httpx.HTTPError as exc:
                last_error = str(exc)
                log.warning("Trakt request to %s failed: %s", path, exc)
            else:
                if response.status_code == 429:
                    wait = float(response.headers.get("Retry-After", 2 * attempt))
                    log.warning("Trakt rate limited us; waiting %.0fs.", wait)
                    time.sleep(min(wait, 60))
                    continue
                if response.status_code == 401:
                    raise TraktAuthError(
                        "Trakt rejected the authorisation. Reconnect the account in Settings."
                    )
                if response.status_code >= 500:
                    last_error = f"HTTP {response.status_code}"
                    log.warning("Trakt returned %s for %s.", response.status_code, path)
                else:
                    return response

            if attempt < max_attempts:
                time.sleep(2**attempt)

        raise TraktError(f"Trakt request to {path} failed after {max_attempts} tries: {last_error}")

    def _get_json(self, path: str, *, account: str | None = None, params=None) -> Any:
        response = self._request("GET", path, account=account, params=params)
        if response.status_code != 200:
            raise TraktError(f"Trakt returned HTTP {response.status_code} for {path}.")
        return response.json()

    # -- OAuth device flow -------------------------------------------------- #

    def device_code(self) -> dict[str, Any]:
        if not self.cfg.configured:
            raise TraktAuthError("Set the Trakt client ID and secret first.")
        response = self._client.post(
            f"{BASE}/oauth/device/code",
            headers=self._base_headers(),
            json={"client_id": self.cfg.client_id},
        )
        if response.status_code not in (200, 201):
            raise TraktError(
                f"Trakt would not issue a device code (HTTP {response.status_code}). "
                "Check the client ID."
            )
        return response.json()

    def device_token(self, device_code: str) -> tuple[str, dict[str, Any] | None]:
        """Poll once for the device token.

        Returns ``(status, payload)`` where status is one of ``ok``, ``pending``,
        ``slow_down``, ``expired``, ``denied``, ``used`` or ``invalid``.
        """
        response = self._client.post(
            f"{BASE}/oauth/device/token",
            headers=self._base_headers(),
            json={
                "code": device_code,
                "client_id": self.cfg.client_id,
                "client_secret": self.cfg.client_secret,
            },
        )
        status_map = {
            400: "pending",
            404: "invalid",
            409: "used",
            410: "expired",
            418: "denied",
            429: "slow_down",
        }
        if response.status_code == 200:
            return "ok", response.json()
        return status_map.get(response.status_code, "invalid"), None

    def whoami(self, access_token: str) -> str:
        headers = self._base_headers()
        headers["Authorization"] = f"Bearer {access_token}"
        response = self._client.get(f"{BASE}/users/me", headers=headers)
        if response.status_code != 200:
            raise TraktError("Could not read the Trakt profile for the new token.")
        data = response.json()
        return data.get("username") or data.get("ids", {}).get("slug") or "trakt-user"

    def validate(self) -> bool:
        """Cheap check that the client ID works at all."""
        response = self._request("GET", "/movies/anticipated", params={"limit": 1})
        return response.status_code == 200

    # -- list discovery ----------------------------------------------------- #

    def user_lists(self, account: str) -> list[dict[str, Any]]:
        """The account's own lists plus the ones it has liked."""
        results: list[dict[str, Any]] = []

        for item in self._get_json(f"/users/{account}/lists", account=account) or []:
            results.append(self._describe_list(item, account, owned=True))

        try:
            liked = self._get_json("/users/likes/lists", account=account, params={"limit": 100})
        except TraktError as exc:
            log.debug("Could not read liked lists for %s: %s", account, exc)
            liked = []

        for entry in liked or []:
            item = entry.get("list") or {}
            owner = (item.get("user") or {}).get("ids", {}).get("slug", "")
            if item:
                results.append(self._describe_list(item, owner, owned=False))

        return results

    @staticmethod
    def _describe_list(item: dict[str, Any], owner: str, owned: bool) -> dict[str, Any]:
        slug = (item.get("ids") or {}).get("slug") or str((item.get("ids") or {}).get("trakt", ""))
        return {
            "name": item.get("name", slug),
            "owner": owner,
            "slug": slug,
            "url": f"{owner}/{slug}",
            "item_count": item.get("item_count", 0),
            "privacy": item.get("privacy", "private"),
            "owned": owned,
        }

    # -- item fetching ------------------------------------------------------ #

    def fetch(self, source: Source, media_type: str, max_items: int = 0) -> list[dict[str, Any]]:
        """Fetch and normalise items for a source definition.

        Returns a list of bare Trakt movie/show objects (``extended=full``).
        """
        path, needs_auth, paged = self._resolve(source, media_type)
        account = source.account or (self.cfg.default_account() if needs_auth else "")
        if needs_auth and not account:
            raise TraktAuthError(
                f"The {source.type!r} source needs a connected Trakt account. "
                "Connect one in Settings."
            )

        params: dict[str, Any] = {"extended": "full"}
        raw = (
            self._fetch_paged(path, account or None, params, max_items)
            if paged
            else self._fetch_single(path, account or None, params, max_items)
        )
        return self._normalise(raw, media_type, source.type)

    def _resolve(self, source: Source, media_type: str) -> tuple[str, bool, bool]:
        """Map a source to ``(path, needs_auth, supports_pagination)``."""
        plural = "movies" if media_type == "movie" else "shows"
        kind = source.type

        if kind == "watchlist":
            return f"/users/{{account}}/watchlist/{plural}", True, True
        if kind == "collection":
            return f"/users/{{account}}/collection/{plural}", True, False
        if kind == "recommended":
            return f"/recommendations/{plural}", True, False
        if kind == "list":
            owner, slug = parse_list_url(source.list_url)
            return f"/users/{owner}/lists/{slug}/items/{plural}", bool(source.account), True
        if kind == "person":
            if not source.person:
                raise TraktError("This source needs a person name.")
            return f"/people/{_slugify_person(source.person)}/{plural}", False, False
        if kind == "boxoffice":
            if media_type != "movie":
                raise TraktError("Box office is a movies-only Trakt list.")
            return "/movies/boxoffice", False, False
        if kind in ("watched", "played"):
            return f"/{plural}/{kind}/{source.period}", False, True
        if kind in ("trending", "popular", "anticipated"):
            return f"/{plural}/{kind}", False, True

        raise TraktError(f"Unknown Trakt source type {kind!r}.")

    def _fetch_single(
        self, path: str, account: str | None, params: dict[str, Any], max_items: int
    ) -> Any:
        if max_items:
            params = {**params, "limit": min(max_items, PAGE_SIZE)}
        return self._get_json(self._fill(path, account), account=account, params=params)

    def _fetch_paged(
        self, path: str, account: str | None, params: dict[str, Any], max_items: int
    ) -> list[Any]:
        collected: list[Any] = []
        page = 1
        # Without a limit we still stop at MAX_TRAKT_PAGES so a huge list can't
        # hold the scheduler hostage.
        page_cap = MAX_TRAKT_PAGES
        if max_items:
            # Over-fetch, because filtering happens after this.
            page_cap = min(page_cap, max(1, (max_items * 4 + PAGE_SIZE - 1) // PAGE_SIZE))

        resolved = self._fill(path, account)
        while page <= page_cap:
            response = self._request(
                "GET",
                resolved,
                account=account,
                params={**params, "limit": PAGE_SIZE, "page": page},
            )
            if response.status_code != 200:
                raise TraktError(f"Trakt returned HTTP {response.status_code} for {resolved}.")

            batch = response.json()
            if not isinstance(batch, list) or not batch:
                break
            collected.extend(batch)

            total_pages = int(response.headers.get("X-Pagination-Page-Count", 0) or 0)
            if total_pages and page >= total_pages:
                break
            if not total_pages and len(batch) < PAGE_SIZE:
                break
            page += 1
            time.sleep(0.4)

        if page > page_cap:
            log.warning(
                "Stopped after %d pages (%d items) for %s; raise SIDECARR_MAX_TRAKT_PAGES "
                "if the list is longer than this.",
                page_cap,
                len(collected),
                resolved,
            )
        return collected

    @staticmethod
    def _fill(path: str, account: str | None) -> str:
        return path.replace("{account}", account or "me")

    @staticmethod
    def _normalise(raw: Any, media_type: str, source_type: str) -> list[dict[str, Any]]:
        """Flatten Trakt's several response shapes into bare media objects.

        Trakt returns bare objects for ``/movies/popular``, wrapped objects for
        trending and list items, and a ``{"cast": [...]}`` envelope for people.
        """
        key = "movie" if media_type == "movie" else "show"

        if isinstance(raw, dict):
            entries: Iterable[Any] = raw.get("cast") or []
            if source_type == "person":
                # Drop narrator/self credits the way traktarr did.
                entries = [
                    item
                    for item in entries
                    if not _is_non_acting_role(item.get("character", ""))
                ]
        elif isinstance(raw, list):
            entries = raw
        else:
            return []

        seen: set[Any] = set()
        results: list[dict[str, Any]] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            obj = entry.get(key) if isinstance(entry.get(key), dict) else None
            if obj is None and "title" in entry:
                obj = entry
            if not isinstance(obj, dict):
                continue

            trakt_id = (obj.get("ids") or {}).get("trakt")
            marker = trakt_id if trakt_id is not None else (obj.get("title"), obj.get("year"))
            if marker in seen:
                continue
            seen.add(marker)
            results.append(obj)

        return results


def _is_non_acting_role(character: str) -> bool:
    lowered = (character or "").strip().lower()
    return not lowered or "narrat" in lowered or "himself" in lowered or "herself" in lowered
