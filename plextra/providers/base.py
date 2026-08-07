"""The provider abstraction: one interface over every list source.

A provider turns "some list, somewhere" into a list of :class:`MediaItem`.
Everything downstream - filtering, sorting, ID resolution, adding - works on
``MediaItem`` and never needs to know which site the list came from.

Providers also describe themselves (:meth:`Provider.describe`), and the web UI
renders the list editor from that description, so adding a provider does not
mean editing the front end.
"""

from __future__ import annotations

import logging
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Iterable

log = logging.getLogger(__name__)

# The ID each target needs: Radarr keys off TMDb, Sonarr off TVDb.
TARGET_ID = {"movie": "tmdb", "show": "tvdb"}


class ProviderError(Exception):
    """Any failure fetching from a list source."""


class ProviderAuthError(ProviderError):
    """Credentials are missing, rejected, or expired."""


# --------------------------------------------------------------------------- #
# The normalised item
# --------------------------------------------------------------------------- #


@dataclass
class MediaItem:
    """One movie or show, however it was described upstream.

    Providers vary wildly in how much metadata they return. Trakt and TMDb are
    rich; an IMDb list or a plain custom URL may give little more than an ID.
    Every field except ``ids`` is therefore optional, and filters treat missing
    metadata as "cannot judge" rather than pretending it passed.
    """

    ids: dict[str, Any] = field(default_factory=dict)
    title: str = ""
    year: int | None = None
    genres: list[str] = field(default_factory=list)
    country: str | None = None
    language: str | None = None
    runtime: int | None = None
    rating: float | None = None
    votes: int | None = None
    network: str | None = None
    released: str | None = None

    def target_id(self, media_type: str) -> int | None:
        """The ID Radarr/Sonarr needs, if this item already carries it."""
        return self.numeric_id(TARGET_ID.get(media_type, "tmdb"))

    def numeric_id(self, key: str) -> int | None:
        value = self.ids.get(key)
        try:
            return int(value) if value not in (None, "") else None
        except (TypeError, ValueError):
            return None

    @property
    def imdb_id(self) -> str | None:
        value = self.ids.get("imdb")
        if not value:
            return None
        value = str(value).strip()
        return value if value.startswith("tt") else None

    @property
    def label(self) -> str:
        title = self.title or "Untitled"
        return f"{title} ({self.year})" if self.year else title

    def dedupe_key(self) -> tuple[str, Any]:
        """Prefer a real ID; fall back to title/year so dupes still collapse."""
        for key in ("tmdb", "tvdb", "imdb", "trakt"):
            if self.ids.get(key):
                return (key, str(self.ids[key]))
        return ("title", (self.title.lower(), self.year))


def set_id(ids: dict[str, Any], key: str, value: Any) -> None:
    """Store an ID, dropping the blanks and zeroes upstreams like to send."""
    if value in (None, "", 0, "0"):
        return
    if key == "imdb":
        text = str(value).strip()
        if text.startswith("tt"):
            ids[key] = text
        return
    try:
        ids[key] = int(value)
    except (TypeError, ValueError):
        pass


def parse_year(value: Any) -> int | None:
    """Accept a year, a full date, or a timestamp and return the year."""
    if isinstance(value, int) and 1800 < value < 2200:
        return value
    text = str(value or "")
    match = re.match(r"(\d{4})", text)
    if match:
        year = int(match.group(1))
        if 1800 < year < 2200:
            return year
    return None


def dedupe(items: Iterable[MediaItem]) -> list[MediaItem]:
    seen: set[tuple[str, Any]] = set()
    result: list[MediaItem] = []
    for item in items:
        key = item.dedupe_key()
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


# --------------------------------------------------------------------------- #
# Self-description, which the web UI renders
# --------------------------------------------------------------------------- #


@dataclass
class SourceField:
    """One provider-specific input in the list editor."""

    key: str
    label: str
    placeholder: str = ""
    help: str = ""
    required: bool = True
    kind: str = "text"  # text | select
    choices: list[dict[str, str]] = field(default_factory=list)
    default: str = ""

    def describe(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "placeholder": self.placeholder,
            "help": self.help,
            "required": self.required,
            "kind": self.kind,
            "choices": self.choices,
            "default": self.default,
        }


@dataclass
class SourceType:
    """One kind of list a provider can fetch."""

    key: str
    label: str
    media: tuple[str, ...] = ("movie", "show")
    fields: tuple[SourceField, ...] = ()
    needs_account: bool = False
    help: str = ""

    def describe(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "media": list(self.media),
            "fields": [f.describe() for f in self.fields],
            "needs_account": self.needs_account,
            "help": self.help,
        }


# --------------------------------------------------------------------------- #
# The provider itself
# --------------------------------------------------------------------------- #


class Provider(ABC):
    key: str = ""
    name: str = ""
    blurb: str = ""
    # Free-text hint shown when the provider needs credentials it does not have.
    setup_hint: str = ""
    # Whether this provider can enumerate the user's own lists for the picker.
    can_pick_lists: bool = False
    source_types: tuple[SourceType, ...] = ()

    def __init__(self, config: Any) -> None:
        self.config = config

    # -- lifecycle --------------------------------------------------------- #

    def close(self) -> None:
        """Release anything held open. HTTP providers override this."""

    def __enter__(self) -> "Provider":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- capability -------------------------------------------------------- #

    def configured(self) -> bool:
        """Whether this provider has everything it needs to run."""
        return True

    def accounts(self) -> list[str]:
        """Named accounts to choose between, for providers that have them."""
        return []

    def source(self, key: str) -> SourceType | None:
        return next((s for s in self.source_types if s.key == key), None)

    def media_types(self) -> set[str]:
        return {media for source in self.source_types for media in source.media}

    # -- work -------------------------------------------------------------- #

    @abstractmethod
    def fetch(self, source: Any, media_type: str, max_items: int = 0) -> list[MediaItem]:
        """Return the items in this list, newest/most-relevant order preserved."""

    def resolve_ids(self, item: MediaItem, media_type: str) -> None:
        """Best-effort fill of a missing target ID, using the provider's own API.

        Called only for items that reached the point of being added, so a
        provider that needs an extra request per item does not pay for the whole
        list. Radarr/Sonarr's own lookup is tried afterwards regardless.
        """

    def validate(self) -> bool:
        """Cheap credential check for the settings screen."""
        return self.configured()

    # -- description ------------------------------------------------------- #

    def describe(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "name": self.name,
            "blurb": self.blurb,
            "setup_hint": self.setup_hint,
            "can_pick_lists": self.can_pick_lists,
            "configured": self.configured(),
            "accounts": self.accounts(),
            "media": sorted(self.media_types()),
            "sources": [source.describe() for source in self.source_types],
        }
