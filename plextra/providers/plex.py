"""Plex: your Plex Discover watchlist."""

from __future__ import annotations

import logging
from typing import Any

from .base import (
    MediaItem,
    Provider,
    ProviderAuthError,
    ProviderError,
    SourceType,
    parse_year,
    set_id,
)
from .http import HttpMixin

log = logging.getLogger(__name__)

DISCOVER = "https://discover.provider.plex.tv"
PAGE_SIZE = 100


class PlexProvider(HttpMixin, Provider):
    key = "plex"
    name = "Plex"
    blurb = "The watchlist you build in Plex Discover, on any device."
    setup_hint = (
        "Add a Plex token in Settings. Find one by opening any item in Plex Web, "
        "choosing Get Info, then View XML, and copying X-Plex-Token from the URL."
    )

    source_types = (SourceType("watchlist", "My Plex watchlist"),)

    @property
    def token(self) -> str:
        return (self.config.plex.token or "").strip()

    def configured(self) -> bool:
        return bool(self.token)

    def validate(self) -> bool:
        if not self.configured():
            raise ProviderAuthError("No Plex token configured.")
        self._page("movie", 0, 1)
        return True

    def _page(self, plex_type: str, offset: int, size: int) -> dict[str, Any]:
        if not self.configured():
            raise ProviderAuthError("No Plex token configured.")
        payload = self.get_json(
            f"{DISCOVER}/library/sections/watchlist/all",
            params={
                # 1 is Plex's code for movies, 2 for shows.
                "type": 1 if plex_type == "movie" else 2,
                "includeGuids": 1,
                "X-Plex-Container-Start": offset,
                "X-Plex-Container-Size": size,
                "X-Plex-Token": self.token,
            },
            headers={"Accept": "application/json"},
        )
        container = payload.get("MediaContainer") if isinstance(payload, dict) else None
        if container is None:
            raise ProviderError("Plex returned an unexpected watchlist response.")
        return container

    def fetch(self, source: Any, media_type: str, max_items: int = 0) -> list[MediaItem]:
        if source.type != "watchlist":
            raise ProviderError(f"Unknown Plex source type {source.type!r}.")

        items: list[MediaItem] = []
        offset = 0
        while True:
            container = self._page(media_type, offset, PAGE_SIZE)
            entries = container.get("Metadata") or []
            if not entries:
                break
            items.extend(self._items(entries, media_type))

            total = int(container.get("totalSize") or container.get("size") or 0)
            offset += PAGE_SIZE
            if not total or offset >= total:
                break
            if max_items and len(items) >= max_items * 4:
                break

        return items

    @staticmethod
    def _items(entries: list[dict[str, Any]], media_type: str) -> list[MediaItem]:
        wanted = "movie" if media_type == "movie" else "show"
        items: list[MediaItem] = []

        for entry in entries:
            if not isinstance(entry, dict):
                continue
            if str(entry.get("type", wanted)).lower() != wanted:
                continue

            ids: dict[str, Any] = {}
            # Plex carries the real IDs as guids like "tmdb://603".
            for guid in entry.get("Guid") or []:
                value = guid.get("id") if isinstance(guid, dict) else str(guid)
                if not value or "://" not in str(value):
                    continue
                scheme, _, ident = str(value).partition("://")
                if scheme in ("tmdb", "tvdb", "imdb"):
                    set_id(ids, scheme, ident)
            if not ids:
                continue

            genres = [
                str(g.get("tag", "")).lower()
                for g in entry.get("Genre") or []
                if isinstance(g, dict) and g.get("tag")
            ]
            duration = entry.get("duration")
            items.append(
                MediaItem(
                    ids=ids,
                    title=entry.get("title") or "",
                    year=parse_year(entry.get("year") or entry.get("originallyAvailableAt")),
                    genres=genres,
                    rating=_float(entry.get("rating") or entry.get("audienceRating")),
                    # Plex reports duration in milliseconds.
                    runtime=(int(duration) // 60000 if isinstance(duration, int) and duration else None),
                    released=entry.get("originallyAvailableAt") or None,
                )
            )
        return items


def _float(value: Any) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None
