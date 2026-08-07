"""MDBList: user lists, the public top lists, and your MDBList watchlist."""

from __future__ import annotations

import logging
import re
from typing import Any

from .base import (
    MediaItem,
    Provider,
    ProviderAuthError,
    ProviderError,
    SourceField,
    SourceType,
    parse_year,
    set_id,
)
from .http import HttpMixin

log = logging.getLogger(__name__)

BASE = "https://api.mdblist.com"

_LIST = SourceField(
    "list_url",
    "List URL, user/list, or list ID",
    placeholder="https://mdblist.com/lists/someone/my-list",
    help="Any of the MDBList URL, 'user/list-slug', or the numeric list ID.",
)


def parse_list_ref(value: str) -> tuple[str, str]:
    """Return ``("id", "123")`` or ``("slug", "user/list")`` for a list reference."""
    text = (value or "").strip()
    if not text:
        raise ProviderError("No MDBList list given.")

    if text.isdigit():
        return "id", text

    match = re.search(r"/lists/([^/]+)/([^/?#]+)", text)
    if match:
        return "slug", f"{match.group(1)}/{match.group(2)}"

    parts = [p for p in text.split("/") if p]
    if len(parts) == 2:
        return "slug", f"{parts[0]}/{parts[1]}"

    raise ProviderError(
        f"{value!r} is not an MDBList list. Use the list URL, 'user/list-slug', "
        "or the numeric list ID."
    )


class MdblistProvider(HttpMixin, Provider):
    key = "mdblist"
    name = "MDBList"
    blurb = "Any MDBList list, the public top lists, or your own watchlist."
    setup_hint = "Add an MDBList API key in Settings. Get one free from mdblist.com/preferences."
    can_pick_lists = True

    source_types = (
        SourceType("list", "A list", fields=(_LIST,)),
        SourceType("my_lists", "One of my lists", fields=(
            SourceField("list_url", "Which list", placeholder="my-list", help="The list name, or leave blank to see them on save."),
        )),
        SourceType("watchlist", "My MDBList watchlist"),
        SourceType("top", "Top lists on MDBList"),
    )

    @property
    def api_key(self) -> str:
        return (self.config.mdblist.api_key or "").strip()

    def configured(self) -> bool:
        return bool(self.api_key)

    def validate(self) -> bool:
        if not self.configured():
            raise ProviderAuthError("No MDBList API key configured.")
        self._get("/user")
        return True

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        if not self.configured():
            raise ProviderAuthError("No MDBList API key configured.")
        return self.get_json(f"{BASE}{path}", params={"apikey": self.api_key, **(params or {})})

    # -- fetching ------------------------------------------------------------ #

    def fetch(self, source: Any, media_type: str, max_items: int = 0) -> list[MediaItem]:
        kind = source.type

        if kind == "watchlist":
            return self._items(self._get("/watchlist/items"), media_type)

        if kind == "top":
            lists = self._get("/lists/top")
            items: list[MediaItem] = []
            for entry in (lists or [])[:5]:
                list_id = entry.get("id")
                if not list_id:
                    continue
                items.extend(self._items(self._get(f"/lists/{list_id}/items"), media_type))
                if max_items and len(items) >= max_items * 4:
                    break
            return items

        if kind in ("list", "my_lists"):
            ref_kind, ref = parse_list_ref(source.get("list_url"))
            path = f"/lists/{ref}/items" if ref_kind == "id" else f"/lists/{ref}/items"
            return self._items(self._get(path), media_type)

        raise ProviderError(f"Unknown MDBList source type {kind!r}.")

    def my_lists(self) -> list[dict[str, Any]]:
        """For the list picker in the UI."""
        payload = self._get("/lists/user")
        return [
            {
                "name": entry.get("name", ""),
                "url": str(entry.get("id", "")),
                "item_count": entry.get("items", 0),
                "owner": entry.get("user_name", ""),
                "owned": True,
            }
            for entry in (payload or [])
            if isinstance(entry, dict)
        ]

    # -- conversion ---------------------------------------------------------- #

    def _items(self, payload: Any, media_type: str) -> list[MediaItem]:
        if isinstance(payload, dict):
            bucket = "movies" if media_type == "movie" else "shows"
            entries = payload.get(bucket)
            if entries is None:
                entries = payload.get("results") or payload.get("items") or []
        else:
            entries = payload or []

        wanted = "movie" if media_type == "movie" else "show"
        items: list[MediaItem] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            # A mixed list tags each row; an already-split one does not.
            mediatype = str(entry.get("mediatype") or entry.get("type") or wanted).lower()
            if mediatype in ("movie", "show", "series", "tv") and _normalise(mediatype) != wanted:
                continue

            ids: dict[str, Any] = {}
            set_id(ids, "imdb", entry.get("imdb_id"))
            set_id(ids, "tvdb", entry.get("tvdb_id") or entry.get("tvdbid"))
            set_id(ids, "tmdb", entry.get("tmdb_id") or entry.get("tmdbid"))
            if not ids:
                continue

            items.append(
                MediaItem(
                    ids=ids,
                    title=entry.get("title") or "",
                    year=parse_year(entry.get("release_year") or entry.get("year") or entry.get("released")),
                    language=(entry.get("language") or None),
                    rating=_float(entry.get("score") or entry.get("rating")),
                )
            )
        return items


def _normalise(mediatype: str) -> str:
    return "movie" if mediatype == "movie" else "show"


def _float(value: Any) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None
