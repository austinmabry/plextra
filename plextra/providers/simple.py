"""The providers that are just a URL: StevenLu, and any custom list.

Between them these cover Radarr's StevenLu, RSS Import and custom list options,
and Sonarr's custom list.
"""

from __future__ import annotations

import logging
from typing import Any

from .base import MediaItem, Provider, ProviderError, SourceField, SourceType, dedupe
from .http import HttpMixin
from .payload import parse_any

log = logging.getLogger(__name__)

STEVENLU_URL = "https://popular-movies-data.stevenlu.com/movies.json"


class StevenLuProvider(HttpMixin, Provider):
    key = "stevenlu"
    name = "StevenLu"
    blurb = "StevenLu's popular movies list. No account, no key, movies only."

    source_types = (SourceType("popular", "Popular movies", media=("movie",)),)

    def fetch(self, source: Any, media_type: str, max_items: int = 0) -> list[MediaItem]:
        if media_type != "movie":
            raise ProviderError("StevenLu's list is movies only.")
        return parse_any(self.get_json(STEVENLU_URL), media_type, id_hint="imdb")


class CustomProvider(HttpMixin, Provider):
    key = "custom"
    name = "Custom list"
    blurb = "Any URL that returns a list: JSON, an RSS feed, or plain IDs."

    source_types = (
        SourceType(
            "url",
            "A URL",
            fields=(
                SourceField(
                    "url",
                    "List URL",
                    placeholder="https://example.com/my-list.json",
                    help="Must be reachable from inside this container.",
                ),
                SourceField(
                    "id_hint",
                    "Bare numbers are",
                    kind="select",
                    required=False,
                    default="",
                    help="Only used when the feed has plain numbers instead of named ID fields.",
                    choices=[
                        {"value": "", "label": "Match the media type (TMDb / TVDb)"},
                        {"value": "tmdb", "label": "TMDb IDs"},
                        {"value": "tvdb", "label": "TVDb IDs"},
                        {"value": "imdb", "label": "IMDb IDs"},
                    ],
                ),
            ),
            help=(
                "Understands Sonarr's custom-list JSON ({title, tvdbId, tmdbId, imdbId}), "
                "StevenLu's ({title, imdb_id}), MDBList's, an RSS/Atom feed, or just a "
                "list of IDs."
            ),
        ),
    )

    def fetch(self, source: Any, media_type: str, max_items: int = 0) -> list[MediaItem]:
        url = (source.get("url") or "").strip()
        if not url:
            raise ProviderError("This list needs a URL.")
        if not url.lower().startswith(("http://", "https://")):
            raise ProviderError(f"{url!r} is not an http(s) URL.")

        text = self.get_text(url, headers={"Accept": "application/json, application/xml, text/plain"})
        items = parse_any(text, media_type, id_hint=source.get("id_hint") or "")
        if not items:
            raise ProviderError(
                "Nothing usable came back from that URL. Plextra needs JSON, an RSS "
                "feed, or a list of IDs, and each entry needs a TMDb, TVDb or IMDb ID."
            )
        return dedupe(items)
