"""Item filtering, ported from traktarr's blacklist semantics.

Differences from traktarr, on purpose:

* Every numeric filter is disabled at ``0``. traktarr shipped
  ``blacklisted_min_year: 2000`` / ``blacklisted_max_year: 2019`` as defaults,
  which quietly discarded anything newer than 2019 until you noticed.
* Country and language matching is exact rather than substring, so ``us`` no
  longer also matches ``rus``.
"""

from __future__ import annotations

from typing import Any

from .config import Filters


def external_id(item: dict[str, Any], media_type: str) -> int | None:
    """Radarr keys off TMDb, Sonarr off TVDb."""
    ids = item.get("ids") or {}
    key = "tmdb" if media_type == "movie" else "tvdb"
    value = ids.get(key)
    try:
        return int(value) if value else None
    except (TypeError, ValueError):
        return None


def item_year(item: dict[str, Any]) -> int | None:
    year = item.get("year")
    if isinstance(year, int) and year > 0:
        return year
    aired = item.get("first_aired") or item.get("released") or ""
    if isinstance(aired, str) and len(aired) >= 4 and aired[:4].isdigit():
        return int(aired[:4])
    return None


def describe(item: dict[str, Any]) -> str:
    year = item_year(item)
    title = item.get("title") or "Untitled"
    return f"{title} ({year})" if year else title


def _check_allowed(value: Any, allowed: list[str], label: str) -> str | None:
    """``[]`` allows anything present, ``["ignore"]`` allows anything at all."""
    if not allowed:
        return None
    if any(entry.strip().lower() == "ignore" for entry in allowed):
        return None
    if not value:
        return f"no {label} listed on Trakt"
    if not any(str(value).strip().lower() == entry.strip().lower() for entry in allowed):
        return f"{label} is {str(value).upper()}"
    return None


def evaluate(item: dict[str, Any], media_type: str, filters: Filters) -> str | None:
    """Return the reason this item is filtered out, or None if it passes."""
    title = item.get("title") or ""
    if not title:
        return "no title on Trakt"

    ident = external_id(item, media_type)
    if ident is not None and ident in set(filters.blacklisted_ids):
        return f"blacklisted ID {ident}"

    lowered_title = title.lower()
    for keyword in filters.blacklisted_title_keywords:
        if keyword and keyword.lower() in lowered_title:
            return f"title contains {keyword!r}"

    if filters.min_year or filters.max_year:
        year = item_year(item)
        if not year:
            return "no release year on Trakt"
        if filters.min_year and year < filters.min_year:
            return f"released {year}, before {filters.min_year}"
        if filters.max_year and year > filters.max_year:
            return f"released {year}, after {filters.max_year}"

    if filters.min_runtime or filters.max_runtime:
        runtime = item.get("runtime")
        if not isinstance(runtime, int) or runtime <= 0:
            return "no runtime on Trakt"
        if filters.min_runtime and runtime < filters.min_runtime:
            return f"runtime {runtime}m, under {filters.min_runtime}m"
        if filters.max_runtime and runtime > filters.max_runtime:
            return f"runtime {runtime}m, over {filters.max_runtime}m"

    reason = _check_allowed(item.get("country"), filters.allowed_countries, "country")
    if reason:
        return reason

    reason = _check_allowed(item.get("language"), filters.allowed_languages, "language")
    if reason:
        return reason

    if filters.blacklisted_genres:
        genres = {str(g).lower() for g in (item.get("genres") or [])}
        for genre in filters.blacklisted_genres:
            if genre and genre.lower() in genres:
                return f"blacklisted genre {genre}"

    if media_type == "show" and filters.blacklisted_networks:
        network = item.get("network") or ""
        for entry in filters.blacklisted_networks:
            if entry and entry.lower() in network.lower():
                return f"blacklisted network {network}"

    if filters.min_rating:
        rating = item.get("rating") or 0
        if float(rating) < filters.min_rating:
            return f"rating {float(rating):.1f}, under {filters.min_rating}"

    if filters.min_votes:
        votes = item.get("votes") or 0
        if int(votes) < filters.min_votes:
            return f"{int(votes)} votes, under {filters.min_votes}"

    return None


def sort_items(
    items: list[dict[str, Any]], media_type: str, sort: str
) -> list[dict[str, Any]]:
    """Sort descending by the chosen key; unknown/none keeps Trakt's order."""
    if sort == "votes":
        return sorted(items, key=lambda i: i.get("votes") or 0, reverse=True)
    if sort == "rating":
        return sorted(items, key=lambda i: i.get("rating") or 0, reverse=True)
    if sort == "released":
        key = "released" if media_type == "movie" else "first_aired"
        return sorted(items, key=lambda i: str(i.get(key) or ""), reverse=True)
    return items
