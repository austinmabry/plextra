"""TMDb: lists, collections, companies, keywords, people and the charts.

Mirrors the import lists Radarr ships (TMDb List/Collection/Company/Keyword/
Person/Popular) and adds the show-side equivalents for Sonarr.
"""

from __future__ import annotations

import difflib
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

BASE = "https://api.themoviedb.org/3"
PAGE_SIZE = 20  # TMDb's page size is fixed.

_LIST_ID = SourceField("list_id", "TMDb list ID", placeholder="8253298", help="The number in the list URL.")
_COLLECTION_ID = SourceField("collection_id", "Collection ID", placeholder="10", help="e.g. 10 for Star Wars.")
_COMPANY_ID = SourceField("company_id", "Company ID", placeholder="420", help="e.g. 420 for Marvel Studios.")
_KEYWORD_ID = SourceField("keyword_id", "Keyword ID", placeholder="180547")
_PERSON_ID = SourceField("person_id", "Person ID", placeholder="1032", help="The number in the person's TMDb URL.")
_WINDOW = SourceField(
    "window",
    "Window",
    kind="select",
    required=False,
    default="week",
    choices=[{"value": "day", "label": "Today"}, {"value": "week", "label": "This week"}],
)

# -- Discover ---------------------------------------------------------------- #

SORT_CHOICES = [
    {"value": "popularity.desc", "label": "Most popular"},
    {"value": "primary_release_date.desc", "label": "Newest first"},
    {"value": "primary_release_date.asc", "label": "Oldest first"},
    {"value": "vote_average.desc", "label": "Highest rated"},
    {"value": "vote_count.desc", "label": "Most voted"},
    {"value": "revenue.desc", "label": "Highest grossing"},
    {"value": "title.asc", "label": "Title A-Z"},
]

MONETIZATION_CHOICES = [
    {"value": "", "label": "Any way to watch"},
    {"value": "flatrate", "label": "Included with a subscription"},
    {"value": "free", "label": "Free"},
    {"value": "ads", "label": "Free with ads"},
    {"value": "rent", "label": "Rentable"},
    {"value": "buy", "label": "Buyable"},
]

_REGION = SourceField(
    "watch_region",
    "Region",
    placeholder="US",
    required=False,
    help="Two-letter country code. Required when filtering by streaming service.",
)
_WATCH_PROVIDERS = SourceField(
    "watch_providers",
    "Streaming on",
    placeholder="Netflix, Disney Plus",
    required=False,
    help="Names or TMDb provider IDs, comma separated. Leave blank for any service.",
)
_MONETIZATION = SourceField(
    "monetization",
    "How it is available",
    kind="select",
    required=False,
    default="",
    choices=MONETIZATION_CHOICES,
)
_GENRES = SourceField(
    "with_genres",
    "Genres",
    placeholder="science fiction, thriller",
    required=False,
    help="Names or TMDb genre IDs, comma separated. All of them must match.",
)
_LANGUAGE = SourceField(
    "with_original_language",
    "Original language",
    placeholder="en",
    required=False,
    help="Two-letter language code.",
)
_SORT = SourceField(
    "sort_by",
    "Order",
    kind="select",
    required=False,
    default="popularity.desc",
    choices=SORT_CHOICES,
)
_MIN_VOTES = SourceField(
    "min_votes",
    "Minimum votes",
    placeholder="200",
    required=False,
    help="Worth setting when ordering by rating, or a film with four votes wins.",
)


class TmdbProvider(HttpMixin, Provider):
    key = "tmdb"
    name = "TMDb"
    blurb = "Lists, collections, companies, keywords, people and the TMDb charts."
    setup_hint = "Add a TMDb API key (v3 auth) in Settings. It is free from themoviedb.org."

    source_types = (
        SourceType(
            "discover",
            "Discover / streaming service",
            fields=(
                _REGION,
                _WATCH_PROVIDERS,
                _MONETIZATION,
                _GENRES,
                _LANGUAGE,
                _SORT,
                _MIN_VOTES,
            ),
            help=(
                "Everything on a streaming service in your region, optionally narrowed "
                "by genre and language. Note this is what is available now, not what "
                "was added recently - TMDb does not publish an added-on date. Order by "
                "newest first for something close to a 'new arrivals' list."
            ),
        ),
        SourceType("list", "Custom list", fields=(_LIST_ID,)),
        SourceType("collection", "Collection", media=("movie",), fields=(_COLLECTION_ID,)),
        SourceType("company", "Company", media=("movie",), fields=(_COMPANY_ID,)),
        SourceType("keyword", "Keyword", media=("movie",), fields=(_KEYWORD_ID,)),
        SourceType("person", "Person", fields=(_PERSON_ID,)),
        SourceType("popular", "Popular"),
        SourceType("top_rated", "Top rated"),
        SourceType("trending", "Trending", fields=(_WINDOW,)),
        SourceType("upcoming", "Upcoming", media=("movie",)),
        SourceType("now_playing", "Now playing", media=("movie",)),
        SourceType("on_the_air", "On the air", media=("show",)),
        SourceType("airing_today", "Airing today", media=("show",)),
    )

    def __init__(self, config: Any) -> None:
        super().__init__(config)
        self._genres: dict[str, dict[int, str]] = {}
        self._watch: dict[tuple[str, str], dict[str, int]] = {}

    # -- credentials -------------------------------------------------------- #

    @property
    def api_key(self) -> str:
        return (self.config.tmdb.api_key or "").strip()

    def configured(self) -> bool:
        return bool(self.api_key)

    def validate(self) -> bool:
        if not self.configured():
            raise ProviderAuthError("No TMDb API key configured.")
        self._get("/configuration")
        return True

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        if not self.configured():
            raise ProviderAuthError("No TMDb API key configured.")
        query = {"api_key": self.api_key, **(params or {})}
        return self.get_json(f"{BASE}{path}", params=query)

    # -- fetching ----------------------------------------------------------- #

    def fetch(self, source: Any, media_type: str, max_items: int = 0) -> list[MediaItem]:
        kind = "movie" if media_type == "movie" else "tv"
        source_type = source.type

        if source_type == "discover":
            return self._discover(media_type, self._discover_params(source, media_type), max_items)
        if source_type == "list":
            return self._list(source.get("list_id"), media_type)
        if source_type == "collection":
            payload = self._get(f"/collection/{_number(source.get('collection_id'), 'collection ID')}")
            return self._items(payload.get("parts") or [], media_type)
        if source_type == "person":
            person = _number(source.get("person_id"), "person ID")
            credits = self._get(f"/person/{person}/{kind}_credits")
            return self._items(credits.get("cast") or [], media_type)
        if source_type == "company":
            return self._discover(media_type, {"with_companies": _number(source.get("company_id"), "company ID")}, max_items)
        if source_type == "keyword":
            return self._discover(media_type, {"with_keywords": _number(source.get("keyword_id"), "keyword ID")}, max_items)
        if source_type == "trending":
            window = source.get("window") or "week"
            return self._paged(f"/trending/{kind}/{window}", media_type, max_items)
        if source_type in ("popular", "top_rated", "upcoming", "now_playing", "on_the_air", "airing_today"):
            return self._paged(f"/{kind}/{source_type}", media_type, max_items)

        raise ProviderError(f"Unknown TMDb source type {source_type!r}.")

    def _list(self, list_id: str, media_type: str) -> list[MediaItem]:
        payload = self._get(f"/list/{_number(list_id, 'list ID')}")
        entries = payload.get("items") or []
        # A TMDb list can hold both kinds; keep only what this job targets.
        wanted = "movie" if media_type == "movie" else "tv"
        entries = [e for e in entries if e.get("media_type", wanted) == wanted]
        return self._items(entries, media_type)

    # -- discover ------------------------------------------------------------ #

    def _discover_params(self, source: Any, media_type: str) -> dict[str, Any]:
        kind = "movie" if media_type == "movie" else "tv"
        params: dict[str, Any] = {"sort_by": source.get("sort_by") or "popularity.desc"}

        region = source.get("watch_region").strip().upper()
        wanted_services = _split(source.get("watch_providers"))
        if wanted_services and not region:
            raise ProviderError(
                "Filtering by streaming service needs a region too - TMDb reports "
                "availability per country. Set it to your two-letter country code."
            )
        if region:
            params["watch_region"] = region
        if wanted_services:
            params["with_watch_providers"] = "|".join(
                str(i) for i in self._service_ids(wanted_services, kind, region)
            )
        monetization = source.get("monetization").strip()
        if monetization:
            if not region:
                raise ProviderError(
                    "Filtering by how something is available needs a region too."
                )
            params["with_watch_monetization_types"] = monetization

        genres = _split(source.get("with_genres"))
        if genres:
            params["with_genres"] = ",".join(str(i) for i in self._genre_ids(genres, media_type))

        language = source.get("with_original_language").strip().lower()
        if language:
            params["with_original_language"] = language

        min_votes = _int(source.get("min_votes"))
        if min_votes:
            params["vote_count.gte"] = min_votes

        return params

    def _service_ids(self, wanted: list[str], kind: str, region: str) -> list[int]:
        """Turn "Netflix, Disney Plus" into the provider IDs discover expects."""
        available = self._watch_providers(kind, region)
        resolved: list[int] = []
        unknown: list[str] = []
        for name in wanted:
            if name.isdigit():
                resolved.append(int(name))
                continue
            found = available.get(_normalise(name))
            if found is None:
                unknown.append(name)
            else:
                resolved.append(found)

        if unknown:
            suggestions = _closest(unknown[0], available)
            hint = f" Did you mean {suggestions}?" if suggestions else ""
            raise ProviderError(
                f"TMDb lists no streaming service called {unknown[0]!r} in {region}.{hint}"
            )
        return resolved

    def _watch_providers(self, kind: str, region: str) -> dict[str, int]:
        key = (kind, region)
        if key not in self._watch:
            payload = self._get(f"/watch/providers/{kind}", {"watch_region": region})
            self._watch[key] = {
                _normalise(entry["provider_name"]): int(entry["provider_id"])
                for entry in payload.get("results") or []
                if entry.get("provider_name") and entry.get("provider_id")
            }
            if not self._watch[key]:
                raise ProviderError(
                    f"TMDb knows no streaming services for region {region!r}. "
                    "Check the country code."
                )
        return self._watch[key]

    def _genre_ids(self, wanted: list[str], media_type: str) -> list[int]:
        by_name = {_normalise(name): gid for gid, name in self._genre_map(media_type).items()}
        resolved: list[int] = []
        for name in wanted:
            if name.isdigit():
                resolved.append(int(name))
                continue
            found = by_name.get(_normalise(name))
            if found is None:
                suggestions = _closest(name, by_name)
                hint = f" Did you mean {suggestions}?" if suggestions else ""
                raise ProviderError(f"TMDb has no genre called {name!r}.{hint}")
            resolved.append(found)
        return resolved

    def _discover(self, media_type: str, params: dict[str, Any], max_items: int) -> list[MediaItem]:
        kind = "movie" if media_type == "movie" else "tv"
        return self._paged(f"/discover/{kind}", media_type, max_items, params)

    def _paged(
        self, path: str, media_type: str, max_items: int, params: dict[str, Any] | None = None
    ) -> list[MediaItem]:
        collected: list[MediaItem] = []
        page = 1
        # Over-fetch, because filtering happens after this.
        page_cap = 25 if not max_items else max(1, min(25, (max_items * 4 + PAGE_SIZE - 1) // PAGE_SIZE))

        while page <= page_cap:
            payload = self._get(path, {**(params or {}), "page": page})
            results = payload.get("results") or []
            if not results:
                break
            collected.extend(self._items(results, media_type))
            total = int(payload.get("total_pages") or 0)
            if total and page >= total:
                break
            page += 1

        return collected

    # -- conversion ---------------------------------------------------------- #

    def _genre_map(self, media_type: str) -> dict[int, str]:
        kind = "movie" if media_type == "movie" else "tv"
        if kind not in self._genres:
            try:
                payload = self._get(f"/genre/{kind}/list")
                self._genres[kind] = {
                    int(g["id"]): str(g["name"]).lower() for g in payload.get("genres", [])
                }
            except ProviderError:
                self._genres[kind] = {}
        return self._genres[kind]

    def _items(self, entries: list[dict[str, Any]], media_type: str) -> list[MediaItem]:
        genre_map = self._genre_map(media_type)
        items = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            ids: dict[str, Any] = {}
            set_id(ids, "tmdb", entry.get("id"))
            if not ids:
                continue

            genres = [
                genre_map.get(int(g), "")
                for g in entry.get("genre_ids", [])
                if isinstance(g, (int, str)) and str(g).isdigit()
            ]
            if not genres and isinstance(entry.get("genres"), list):
                genres = [str(g.get("name", "")).lower() for g in entry["genres"] if isinstance(g, dict)]

            countries = entry.get("origin_country") or []
            items.append(
                MediaItem(
                    ids=ids,
                    title=entry.get("title") or entry.get("name") or "",
                    year=parse_year(entry.get("release_date") or entry.get("first_air_date")),
                    genres=[g for g in genres if g],
                    country=(countries[0].lower() if countries else None),
                    language=(entry.get("original_language") or None),
                    rating=_float(entry.get("vote_average")),
                    votes=_int(entry.get("vote_count")),
                    released=entry.get("release_date") or entry.get("first_air_date") or None,
                    # TMDb's list endpoints do not carry runtime; a per-item
                    # request for every candidate is not worth it, so runtime
                    # filters simply have nothing to judge here.
                    runtime=_int(entry.get("runtime")),
                )
            )
        return items

    # -- ID resolution -------------------------------------------------------- #

    def resolve_ids(self, item: MediaItem, media_type: str) -> None:
        """Sonarr needs a TVDb ID, and TMDb only hands out its own."""
        if media_type != "show" or item.ids.get("tvdb"):
            return
        tmdb_id = item.numeric_id("tmdb")
        if not tmdb_id:
            return
        try:
            external = self._get(f"/tv/{tmdb_id}/external_ids")
        except ProviderError as exc:
            log.debug("TMDb external IDs for %s failed: %s", tmdb_id, exc)
            return
        set_id(item.ids, "tvdb", external.get("tvdb_id"))
        set_id(item.ids, "imdb", external.get("imdb_id"))


def _number(value: Any, what: str) -> int:
    text = str(value or "").strip()
    # Tolerate someone pasting the whole URL.
    digits = "".join(ch for ch in text.split("?")[0].rstrip("/").split("/")[-1] if ch.isdigit())
    if not digits:
        raise ProviderError(f"This source needs a numeric TMDb {what}.")
    return int(digits)


def _int(value: Any) -> int | None:
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _float(value: Any) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _split(value: str) -> list[str]:
    return [part.strip() for part in str(value or "").split(",") if part.strip()]


def _normalise(name: str) -> str:
    """Compare service and genre names the forgiving way people type them.

    "Disney Plus", "disney+" and "Disney  Plus" all mean the same service, and
    "Sci-Fi" is TMDb's "Science Fiction" often enough to be worth folding.
    """
    text = str(name or "").strip().lower().replace("+", " plus ")
    text = re.sub(r"[^a-z0-9]+", " ", text).strip()
    return re.sub(r"\s+", " ", text)


def _closest(name: str, options: dict[str, Any]) -> str:
    """A short "did you mean" list, so a typo does not need a docs trip."""
    matches = difflib.get_close_matches(_normalise(name), list(options), n=3, cutoff=0.6)
    if not matches:
        needle = _normalise(name)
        matches = [key for key in options if needle and needle in key][:3]
    return ", ".join(repr(m) for m in matches)
