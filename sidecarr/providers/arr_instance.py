"""Another Radarr or Sonarr instance's library, as a list source.

Matches Radarr's "Radarr" and Sonarr's "Sonarr" import lists: point at a second
instance and mirror what it already has.
"""

from __future__ import annotations

import logging
from typing import Any

from ..clients import ArrError, RadarrClient, SonarrClient
from .base import MediaItem, Provider, ProviderError, SourceField, SourceType, set_id

log = logging.getLogger(__name__)


class ArrInstanceProvider(Provider):
    key = "arr"
    name = "Another Radarr / Sonarr"
    blurb = "Mirror the library of a second Radarr or Sonarr instance."

    source_types = (
        SourceType(
            "library",
            "Its library",
            fields=(
                SourceField("url", "Instance URL", placeholder="http://radarr-4k:7878"),
                SourceField("api_key", "API key", placeholder="its API key"),
                SourceField(
                    "only_monitored",
                    "Include",
                    kind="select",
                    required=False,
                    default="",
                    choices=[
                        {"value": "", "label": "Everything in its library"},
                        {"value": "yes", "label": "Only monitored titles"},
                    ],
                ),
            ),
            help="Use the movies/shows toggle above to say which kind of instance this is.",
        ),
    )

    def fetch(self, source: Any, media_type: str, max_items: int = 0) -> list[MediaItem]:
        if source.type != "library":
            raise ProviderError(f"Unknown source type {source.type!r}.")

        url = (source.get("url") or "").strip()
        api_key = (source.get("api_key") or "").strip()
        if not url or not api_key:
            raise ProviderError("This list needs the other instance's URL and API key.")

        only_monitored = (source.get("only_monitored") or "") == "yes"
        client_cls = RadarrClient if media_type == "movie" else SonarrClient
        id_key = "tmdb" if media_type == "movie" else "tvdb"
        field = "tmdbId" if media_type == "movie" else "tvdbId"

        try:
            with client_cls(url, api_key) as client:
                records = client.movies() if media_type == "movie" else client.series()
        except ArrError as exc:
            raise ProviderError(str(exc)) from exc

        items: list[MediaItem] = []
        for record in records:
            if only_monitored and not record.get("monitored", True):
                continue
            ids: dict[str, Any] = {}
            set_id(ids, id_key, record.get(field))
            set_id(ids, "imdb", record.get("imdbId"))
            if not ids:
                continue
            items.append(
                MediaItem(
                    ids=ids,
                    title=record.get("title") or "",
                    year=record.get("year") or None,
                    genres=[str(g).lower() for g in record.get("genres") or []],
                    runtime=record.get("runtime") or None,
                    network=record.get("network") or None,
                    released=record.get("inCinemas") or record.get("firstAired") or None,
                )
            )
        return items
