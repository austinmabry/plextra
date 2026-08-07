"""IMDb lists and charts.

IMDb has no public API. Radarr reaches these through its own hosted proxy
(api.radarr.video), which is not ours to use, so Plextra reads the public pages
directly and pulls the title IDs out.

That means IMDb items arrive as an IMDb ID and, where the page makes it easy, a
title and year - but no genres, runtime or ratings. Radarr and Sonarr fill in
the rest when they look the title up, so adding works fine; it is the metadata
*filters* that have nothing to judge. Filter an IMDb list by year or genre and
everything drops out. Use the limit instead, or point the same list through
MDBList, which does return full metadata.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from .base import MediaItem, Provider, ProviderError, SourceField, SourceType, parse_year, set_id
from .http import HttpMixin
from .payload import IMDB_RE

log = logging.getLogger(__name__)

CHARTS = {
    "top": ("Top 250 movies", "https://www.imdb.com/chart/top/", "movie"),
    "moviemeter": ("Most popular movies", "https://www.imdb.com/chart/moviemeter/", "movie"),
    "top_english": ("Top rated English movies", "https://www.imdb.com/chart/top-english-movies/", "movie"),
    "boxoffice": ("Top box office", "https://www.imdb.com/chart/boxoffice/", "movie"),
    "toptv": ("Top 250 TV shows", "https://www.imdb.com/chart/toptv/", "show"),
    "tvmeter": ("Most popular TV shows", "https://www.imdb.com/chart/tvmeter/", "show"),
}

_LIST = SourceField(
    "list_id",
    "List ID or URL",
    placeholder="ls123456789",
    help="The ls… ID from the list URL. The list must be public.",
)
_CHART = SourceField(
    "chart",
    "Chart",
    kind="select",
    default="top",
    choices=[{"value": key, "label": label} for key, (label, _, _) in CHARTS.items()],
)


class ImdbProvider(HttpMixin, Provider):
    key = "imdb"
    name = "IMDb"
    blurb = "Public IMDb lists and the IMDb charts. IDs and titles only, no metadata for filtering."

    source_types = (
        SourceType("list", "Public list", fields=(_LIST,)),
        SourceType("chart", "Chart", fields=(_CHART,)),
    )

    def fetch(self, source: Any, media_type: str, max_items: int = 0) -> list[MediaItem]:
        if source.type == "chart":
            key = source.get("chart") or "top"
            if key not in CHARTS:
                raise ProviderError(f"Unknown IMDb chart {key!r}.")
            label, url, chart_media = CHARTS[key]
            if chart_media != media_type:
                raise ProviderError(
                    f"The {label!r} chart is a "
                    f"{'movies' if chart_media == 'movie' else 'shows'} chart; "
                    f"this list is set to {'movies' if media_type == 'movie' else 'shows'}."
                )
        elif source.type == "list":
            url = f"https://www.imdb.com/list/{_list_id(source.get('list_id'))}/"
        else:
            raise ProviderError(f"Unknown IMDb source type {source.type!r}.")

        html = self.get_text(
            url,
            headers={
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "en-US,en;q=0.9",
            },
        )
        items = _extract(html)
        if not items:
            raise ProviderError(
                "No titles were found on that IMDb page. Public lists work; private "
                "ones do not, and IMDb sometimes blocks automated requests."
            )
        return items


def _list_id(value: Any) -> str:
    text = str(value or "").strip()
    match = re.search(r"(ls\d{6,})", text)
    if not match:
        raise ProviderError(
            f"{value!r} is not an IMDb list. Use the ls… ID or the full list URL."
        )
    return match.group(1)


def _extract(html: str) -> list[MediaItem]:
    """Pull titles out of the embedded JSON, falling back to bare IDs."""
    items: list[MediaItem] = []
    seen: set[str] = set()

    # IMDb ships the page data as JSON. Walk it for objects that carry a title
    # ID, which survives their markup changes better than scraping the HTML.
    for blob in re.findall(r'<script[^>]+application/json[^>]*>(.*?)</script>', html, re.S):
        try:
            data = json.loads(blob)
        except ValueError:
            continue
        for node in _walk(data):
            imdb_id = node.get("id")
            if not isinstance(imdb_id, str) or not IMDB_RE.fullmatch(imdb_id):
                continue
            if imdb_id in seen:
                continue
            title = node.get("titleText")
            if isinstance(title, dict):
                title = title.get("text")
            release = node.get("releaseYear")
            year = release.get("year") if isinstance(release, dict) else release
            if not title:
                continue
            seen.add(imdb_id)
            ids: dict[str, Any] = {}
            set_id(ids, "imdb", imdb_id)
            items.append(MediaItem(ids=ids, title=str(title), year=parse_year(year)))

    if items:
        return items

    # Nothing structured survived; take the IDs in page order.
    for imdb_id in IMDB_RE.findall(html):
        if imdb_id in seen:
            continue
        seen.add(imdb_id)
        ids = {}
        set_id(ids, "imdb", imdb_id)
        items.append(MediaItem(ids=ids))
    return items


def _walk(node: Any):
    """Yield every dict in a nested structure."""
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for value in node:
            yield from _walk(value)
