"""Trakt, as a provider.

The HTTP client and the OAuth device flow still live in ``clients/trakt.py``;
this wraps it in the provider interface and normalises Trakt's objects.
"""

from __future__ import annotations

import logging
from typing import Any

from ..clients.trakt import TraktClient, TraktError
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

log = logging.getLogger(__name__)

_LIST_URL = SourceField(
    "list_url",
    "List URL or user/list",
    placeholder="https://trakt.tv/users/someone/lists/my-list",
)
_PERSON = SourceField("person", "Person", placeholder="Denis Villeneuve")
_PERIOD = SourceField(
    "period",
    "Period",
    kind="select",
    default="weekly",
    required=False,
    choices=[
        {"value": "daily", "label": "Daily"},
        {"value": "weekly", "label": "Weekly"},
        {"value": "monthly", "label": "Monthly"},
        {"value": "yearly", "label": "Yearly"},
        {"value": "all", "label": "All time"},
    ],
)


class TraktProvider(Provider):
    key = "trakt"
    name = "Trakt"
    blurb = "Watchlists, custom lists, collections and the Trakt charts."
    setup_hint = "Add a Trakt client ID and secret in Settings, then connect an account."
    can_pick_lists = True

    source_types = (
        SourceType("watchlist", "Watchlist", needs_account=True),
        SourceType("list", "Custom list", fields=(_LIST_URL,)),
        SourceType("collection", "Collection", needs_account=True),
        SourceType("recommended", "Personal recommendations", needs_account=True),
        SourceType("trending", "Trending"),
        SourceType("popular", "Popular"),
        SourceType("anticipated", "Anticipated"),
        SourceType("boxoffice", "Box office", media=("movie",)),
        SourceType("watched", "Most watched", fields=(_PERIOD,)),
        SourceType("played", "Most played", fields=(_PERIOD,)),
        SourceType("person", "By person", fields=(_PERSON,)),
    )

    def __init__(self, config: Any, on_account_update=None) -> None:
        super().__init__(config)
        self._on_account_update = on_account_update

    def configured(self) -> bool:
        return bool(self.config.trakt.client_id)

    def accounts(self) -> list[str]:
        return sorted(self.config.trakt.accounts)

    def validate(self) -> bool:
        if not self.configured():
            raise ProviderAuthError("No Trakt client ID configured.")
        with TraktClient(self.config.trakt) as client:
            return client.validate()

    def fetch(self, source: Any, media_type: str, max_items: int = 0) -> list[MediaItem]:
        if not self.configured():
            raise ProviderAuthError(
                "No Trakt application configured. Add a client ID and secret in Settings."
            )
        try:
            with TraktClient(
                self.config.trakt, on_account_update=self._on_account_update
            ) as client:
                raw = client.fetch(source, media_type, max_items=max_items)
        except TraktError as exc:
            raise ProviderError(str(exc)) from exc
        return [self._item(entry, media_type) for entry in raw]

    @staticmethod
    def _item(entry: dict[str, Any], media_type: str) -> MediaItem:
        ids: dict[str, Any] = {}
        for key in ("tmdb", "tvdb", "trakt"):
            set_id(ids, key, (entry.get("ids") or {}).get(key))
        set_id(ids, "imdb", (entry.get("ids") or {}).get("imdb"))

        return MediaItem(
            ids=ids,
            title=entry.get("title") or "",
            year=parse_year(entry.get("year") or entry.get("first_aired") or entry.get("released")),
            genres=[str(g).lower() for g in entry.get("genres") or []],
            country=entry.get("country") or None,
            language=entry.get("language") or None,
            runtime=entry.get("runtime") if isinstance(entry.get("runtime"), int) else None,
            rating=entry.get("rating") or None,
            votes=entry.get("votes") or None,
            network=entry.get("network") or None,
            released=entry.get("released") or entry.get("first_aired") or None,
        )
