"""Item filtering, ported from traktarr's blacklist semantics.

Filters run on the normalised :class:`~plextra.providers.base.MediaItem`, so
they behave the same whichever site the list came from.

Differences from traktarr, on purpose:

* Every numeric filter is disabled at ``0``. traktarr shipped
  ``blacklisted_min_year: 2000`` / ``blacklisted_max_year: 2019`` as defaults,
  which quietly discarded anything newer than 2019 until you noticed.
* Country and language matching is exact rather than substring, so ``us`` no
  longer also matches ``rus``.

One consequence of covering many providers: a filter can only judge metadata the
provider actually sent. IMDb lists and bare custom URLs carry little more than an
ID, so a year or genre filter will reject everything from them. The reason
recorded against each item names the provider, so this is visible in History
rather than mysterious.
"""

from __future__ import annotations

from typing import Any

from .config import Filters
from .providers.base import MediaItem


def evaluate(
    item: MediaItem, media_type: str, filters: Filters, source_name: str = "the list"
) -> str | None:
    """Return the reason this item is filtered out, or None if it passes."""
    if not item.title and not item.ids:
        return "no title and no ID"

    if filters.blacklisted_ids:
        blacklisted = set(filters.blacklisted_ids)
        for key in ("tmdb", "tvdb"):
            ident = item.numeric_id(key)
            if ident is not None and ident in blacklisted:
                return f"blacklisted ID {ident}"

    if item.title:
        lowered_title = item.title.lower()
        for keyword in filters.blacklisted_title_keywords:
            if keyword and keyword.lower() in lowered_title:
                return f"title contains {keyword!r}"

    if filters.min_year or filters.max_year:
        if not item.year:
            return f"no release year from {source_name}"
        if filters.min_year and item.year < filters.min_year:
            return f"released {item.year}, before {filters.min_year}"
        if filters.max_year and item.year > filters.max_year:
            return f"released {item.year}, after {filters.max_year}"

    if filters.min_runtime or filters.max_runtime:
        runtime = item.runtime
        if not isinstance(runtime, int) or runtime <= 0:
            return f"no runtime from {source_name}"
        if filters.min_runtime and runtime < filters.min_runtime:
            return f"runtime {runtime}m, under {filters.min_runtime}m"
        if filters.max_runtime and runtime > filters.max_runtime:
            return f"runtime {runtime}m, over {filters.max_runtime}m"

    reason = _check_allowed(item.country, filters.allowed_countries, "country", source_name)
    if reason:
        return reason

    reason = _check_allowed(item.language, filters.allowed_languages, "language", source_name)
    if reason:
        return reason

    if filters.blacklisted_genres:
        if not item.genres:
            return f"no genres from {source_name}"
        genres = {str(g).lower() for g in item.genres}
        for genre in filters.blacklisted_genres:
            if genre and genre.lower() in genres:
                return f"blacklisted genre {genre}"

    if media_type == "show" and filters.blacklisted_networks:
        network = item.network or ""
        for entry in filters.blacklisted_networks:
            if entry and entry.lower() in network.lower():
                return f"blacklisted network {network}"

    if filters.min_rating:
        if item.rating is None:
            return f"no rating from {source_name}"
        if float(item.rating) < filters.min_rating:
            return f"rating {float(item.rating):.1f}, under {filters.min_rating}"

    if filters.min_votes:
        if item.votes is None:
            return f"no vote count from {source_name}"
        if int(item.votes) < filters.min_votes:
            return f"{int(item.votes)} votes, under {filters.min_votes}"

    return None


def _check_allowed(
    value: Any, allowed: list[str], label: str, source_name: str
) -> str | None:
    """``[]`` allows anything present, ``["ignore"]`` allows anything at all."""
    if not allowed:
        return None
    if any(entry.strip().lower() == "ignore" for entry in allowed):
        return None
    if not value:
        return f"no {label} from {source_name}"
    if not any(str(value).strip().lower() == entry.strip().lower() for entry in allowed):
        return f"{label} is {str(value).upper()}"
    return None


def sort_items(items: list[MediaItem], media_type: str, sort: str) -> list[MediaItem]:
    """Sort descending by the chosen key; unknown/none keeps the source order."""
    if sort == "votes":
        return sorted(items, key=lambda i: i.votes or 0, reverse=True)
    if sort == "rating":
        return sorted(items, key=lambda i: i.rating or 0, reverse=True)
    if sort == "released":
        return sorted(items, key=lambda i: (str(i.released or ""), i.year or 0), reverse=True)
    return items
