"""Parsing for the loosely-defined list formats.

Radarr and Sonarr's "custom list", their RSS import and the StevenLu list are
all just a URL that returns *something*. This turns any of the shapes those
produce into :class:`MediaItem` objects:

* ``[{"title": ..., "tvdbId": 1, "tmdbId": 2, "imdbId": "tt3"}]`` - Sonarr's
  custom list
* ``[{"title": ..., "imdb_id": "tt3"}]`` - StevenLu, and Radarr's custom list
* ``[{"id": 1, "title": ...}]`` - a bare TMDb-style dump
* ``[603, 604]`` or ``["tt0133093"]`` - just IDs
* ``{"movies": [...], "shows": [...]}`` - MDBList
* RSS/Atom XML - IMDb and Trakt list feeds, Plex RSS
"""

from __future__ import annotations

import json
import re
from typing import Any
from xml.etree import ElementTree

from .base import MediaItem, ProviderError, parse_year, set_id

IMDB_RE = re.compile(r"tt\d{7,10}")

# Every spelling of an ID field seen across the formats above.
ID_ALIASES = {
    "tmdb": ("tmdbid", "tmdb_id", "tmdb", "themoviedbid", "moviedb_id"),
    "tvdb": ("tvdbid", "tvdb_id", "tvdb", "thetvdbid"),
    "imdb": ("imdbid", "imdb_id", "imdb"),
    "trakt": ("traktid", "trakt_id", "trakt"),
}

TITLE_KEYS = ("title", "name", "original_title", "original_name", "movietitle")
YEAR_KEYS = ("year", "release_year", "releaseyear", "released", "release_date", "first_air_date")


def parse_any(payload: Any, media_type: str, *, id_hint: str = "") -> list[MediaItem]:
    """Turn whatever a custom URL returned into items.

    ``id_hint`` says how to read a bare number - a plain list of integers is
    ambiguous, and only the caller knows whether the URL serves movies or shows.
    """
    if isinstance(payload, str):
        payload = _decode(payload)

    entries = _entries(payload, media_type)
    items = [_item(entry, media_type, id_hint) for entry in entries]
    return [item for item in items if item is not None]


def _decode(text: str) -> Any:
    text = text.strip()
    if not text:
        return []
    if text[0] in "[{":
        try:
            return json.loads(text)
        except ValueError:
            pass
    if text.lstrip().startswith("<"):
        return _parse_xml(text)
    # A plain newline- or comma-separated list of IDs.
    tokens = [t.strip() for t in re.split(r"[\s,]+", text) if t.strip()]
    if tokens:
        return tokens
    raise ProviderError("That URL did not return JSON, XML or a list of IDs.")


def _entries(payload: Any, media_type: str) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []

    # MDBList and friends split the two kinds; take the half we asked for.
    bucket = "movies" if media_type == "movie" else "shows"
    if isinstance(payload.get(bucket), list):
        return payload[bucket]

    for key in ("items", "results", "entries", "data", "movies", "shows", "series"):
        if isinstance(payload.get(key), list):
            return payload[key]

    # A single object is a list of one.
    if any(k in payload for k in TITLE_KEYS) or _any_id(payload):
        return [payload]
    return []


def _any_id(entry: dict[str, Any]) -> bool:
    lowered = {k.lower() for k in entry}
    return any(alias in lowered for aliases in ID_ALIASES.values() for alias in aliases)


def _item(entry: Any, media_type: str, id_hint: str) -> MediaItem | None:
    if isinstance(entry, (int, float)):
        ids: dict[str, Any] = {}
        set_id(ids, id_hint or ("tmdb" if media_type == "movie" else "tvdb"), int(entry))
        return MediaItem(ids=ids) if ids else None

    if isinstance(entry, str):
        text = entry.strip()
        ids = {}
        if IMDB_RE.fullmatch(text):
            set_id(ids, "imdb", text)
        elif text.isdigit():
            set_id(ids, id_hint or ("tmdb" if media_type == "movie" else "tvdb"), int(text))
        return MediaItem(ids=ids) if ids else None

    if not isinstance(entry, dict):
        return None

    lowered = {str(k).lower(): v for k, v in entry.items()}
    ids = {}
    for target, aliases in ID_ALIASES.items():
        for alias in aliases:
            if alias in lowered:
                set_id(ids, target, lowered[alias])
                if target in ids:
                    break

    # A bare "id" is whatever the caller said this feed is keyed by.
    if not ids and "id" in lowered:
        set_id(ids, id_hint or ("tmdb" if media_type == "movie" else "tvdb"), lowered["id"])

    # Some feeds only carry the IMDb ID inside a link or guid.
    if not ids:
        for key in ("guid", "link", "url", "imdb_url"):
            match = IMDB_RE.search(str(lowered.get(key, "")))
            if match:
                set_id(ids, "imdb", match.group(0))
                break

    if not ids:
        return None

    title = ""
    for key in TITLE_KEYS:
        if lowered.get(key):
            title = str(lowered[key])
            break

    year = None
    for key in YEAR_KEYS:
        if lowered.get(key):
            year = parse_year(lowered[key])
            if year:
                break
    if year is None and title:
        # "The Matrix (1999)" - the year is often only in the title.
        match = re.search(r"\((\d{4})\)\s*$", title)
        if match:
            year = int(match.group(1))
            title = title[: match.start()].strip()

    genres = lowered.get("genres") or lowered.get("genre") or []
    if isinstance(genres, str):
        genres = [g.strip() for g in genres.split(",") if g.strip()]
    genres = [str(g).lower() for g in genres if isinstance(g, (str, int))]

    return MediaItem(
        ids=ids,
        title=title,
        year=year,
        genres=genres,
        language=lowered.get("language") or lowered.get("original_language") or None,
        country=lowered.get("country") or None,
        runtime=_int(lowered.get("runtime")),
        rating=_float(lowered.get("rating") or lowered.get("score") or lowered.get("vote_average")),
        votes=_int(lowered.get("votes") or lowered.get("vote_count")),
    )


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


def _parse_xml(text: str) -> list[dict[str, Any]]:
    try:
        root = ElementTree.fromstring(text)
    except ElementTree.ParseError as exc:
        raise ProviderError(f"That URL returned XML Sidecarr could not parse: {exc}") from exc

    entries: list[dict[str, Any]] = []
    # RSS <item> and Atom <entry>, namespace or not.
    for node in root.iter():
        tag = node.tag.rsplit("}", 1)[-1].lower()
        if tag not in ("item", "entry"):
            continue
        record: dict[str, Any] = {}
        for child in node:
            child_tag = child.tag.rsplit("}", 1)[-1].lower()
            value = (child.text or "").strip()
            if child_tag == "link" and not value:
                value = child.attrib.get("href", "")
            if value and child_tag not in record:
                record[child_tag] = value
        blob = " ".join(str(v) for v in record.values())
        match = IMDB_RE.search(blob)
        if match:
            record.setdefault("imdb_id", match.group(0))
        if record:
            entries.append(record)
    return entries
