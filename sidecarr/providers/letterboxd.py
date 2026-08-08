"""Letterboxd watchlists, lists and watched films.

Letterboxd has no public API - it has been in closed beta for years - and the
RSS feeds for watchlists and lists answer 403. What does work is the ordinary
web pages, which carry each film's title, year and slug in the markup that drives
their lazy-loaded posters.

So a list page gives a title and a year, and nothing more. Two things follow:

* Metadata filters have nothing to judge. Filter a Letterboxd list by genre or
  runtime and everything drops out. Use the limit, or run the list through
  MDBList, which returns full metadata.
* Items arrive with no ID. The exact TMDb ID is on each film's own page, so
  :meth:`resolve_ids` fetches it - but only for the handful of titles that
  survive filtering and are not already in the library, never for the whole
  list. Anything that route misses falls back to Radarr's title search.

Being a scrape, this breaks when Letterboxd changes their markup. The parsing is
kept to one narrow pattern so a break is obvious and cheap to fix, and a page
that yields nothing recognisable says so rather than reporting an empty list.

Letterboxd is films only, so every source here is movies.
"""

from __future__ import annotations

import html
import logging
import re
from typing import Any

from .base import MediaItem, Provider, ProviderError, SourceField, SourceType
from .http import HttpMixin

log = logging.getLogger(__name__)

BASE = "https://letterboxd.com"

# Letterboxd paginates lists and watchlists; an out-of-range page comes back 200
# with no films, which is the natural stop. The cap is a backstop against a
# markup change turning into an endless walk.
MAX_PAGES = 60

# Each film on a list page is one <div class="react-component"> carrying the
# poster's metadata. Match the tag, then read the attributes out of it.
FILM_TAG_RE = re.compile(r"<div[^>]*\bdata-item-slug=\"[^\"]+\"[^>]*>")
SLUG_RE = re.compile(r"data-item-slug=\"([^\"]+)\"")
NAME_RE = re.compile(r"data-item-name=\"([^\"]*)\"")
LINK_RE = re.compile(r"data-item-link=\"([^\"]*)\"")
YEAR_IN_NAME_RE = re.compile(r"\s*\((\d{4})\)\s*$")

TMDB_ON_FILM_PAGE_RE = re.compile(r"themoviedb\.org/movie/(\d+)")
IMDB_ON_FILM_PAGE_RE = re.compile(r"imdb\.com/title/(tt\d+)")

_USER = SourceField(
    "username",
    "Letterboxd username",
    placeholder="dave",
    help="The name in the profile URL, not the display name.",
)
_LIST_SLUG = SourceField(
    "list_slug",
    "List",
    placeholder="best-of-2026",
    help="The last part of the list URL, or paste the whole URL.",
)


class LetterboxdProvider(HttpMixin, Provider):
    key = "letterboxd"
    name = "Letterboxd"
    blurb = "Watchlists, lists and watched films. Titles and years only, no metadata to filter on."

    source_types = (
        SourceType(
            "watchlist",
            "Someone's watchlist",
            media=("movie",),
            fields=(_USER,),
            help="The profile has to be public.",
        ),
        SourceType(
            "list",
            "A list",
            media=("movie",),
            fields=(_USER, _LIST_SLUG),
            help="Any public list. Paste the list URL into either field and it is worked out.",
        ),
        SourceType(
            "films",
            "Films someone has watched",
            media=("movie",),
            fields=(_USER,),
            help="Everything marked watched. Large profiles run to thousands of titles.",
        ),
    )

    # -- fetching ----------------------------------------------------------- #

    def fetch(self, source: Any, media_type: str, max_items: int = 0) -> list[MediaItem]:
        if media_type != "movie":
            raise ProviderError("Letterboxd only tracks films, so it cannot feed Sonarr.")

        path = self._path(source)
        items: list[MediaItem] = []
        seen: set[str] = set()

        for page in range(1, MAX_PAGES + 1):
            url = f"{BASE}{path}" if page == 1 else f"{BASE}{path}page/{page}/"
            found = self._films(self._page(url), first_page=(page == 1))
            if not found:
                break
            fresh = [item for item in found if item.source_key not in seen]
            seen.update(item.source_key for item in fresh)
            if not fresh:
                # The same page again means pagination stopped advancing.
                break
            items.extend(fresh)
            # Over-fetch, because filtering and the library check come later.
            if max_items and len(items) >= max_items * 4:
                break

        if not items:
            raise ProviderError(
                f"No films found at {BASE}{path}. Check the profile or list is public, "
                "and that the name is the one from the URL."
            )
        log.info("Letterboxd returned %d films from %s.", len(items), path)
        return items

    def _path(self, source: Any) -> str:
        if source.type == "list":
            # A pasted list URL carries the owner too, in whichever field it
            # landed in, so look for one before asking for the parts separately.
            both = f"{source.get('username')} {source.get('list_slug')}"
            match = re.search(r"letterboxd\.com/([^/\s]+)/list/([^/?#\s]+)", both)
            if match:
                return f"/{match.group(1)}/list/{match.group(2)}/"
            user = _handle(source.get("username"), "username")
            return f"/{user}/list/{_handle(source.get('list_slug'), 'list')}/"

        user = _handle(source.get("username"), "username")
        if source.type == "watchlist":
            return f"/{user}/watchlist/"
        if source.type == "films":
            return f"/{user}/films/"
        raise ProviderError(f"Unknown Letterboxd source type {source.type!r}.")

    def _page(self, url: str) -> str:
        try:
            return self.get_text(url)
        except ProviderError as exc:
            # The shared HTTP helper reads 403 as bad credentials and 404 as a
            # missing list, neither of which is the right story for a site we are
            # simply reading pages from.
            message = str(exc)
            if "credentials" in message:
                raise ProviderError(
                    f"Letterboxd refused to serve {url}. Private profiles and lists "
                    "are not readable, and they block their own RSS feeds to scripts."
                ) from exc
            if "404" in message:
                raise ProviderError(
                    f"Letterboxd has nothing at {url}. Check the username is the one "
                    "from the profile URL, and that the list slug is spelled as it "
                    "appears there."
                ) from exc
            raise

    def _films(self, page: str, first_page: bool) -> list[MediaItem]:
        items: list[MediaItem] = []
        for tag in FILM_TAG_RE.findall(page):
            slug_match = SLUG_RE.search(tag)
            if not slug_match:
                continue
            slug = slug_match.group(1)

            title, year = _split_year(_attr(NAME_RE, tag))
            link = _attr(LINK_RE, tag) or f"/film/{slug}/"

            items.append(
                MediaItem(title=title, year=year, source_key=link or slug)
            )

        if first_page and not items and "data-item-slug" not in page:
            # Either the page is not a list, or Letterboxd changed their markup.
            # Both are worth saying out loud rather than reporting "0 films".
            log.warning(
                "Letterboxd page had no recognisable films. If the URL is right, "
                "their markup has probably changed."
            )
        return items

    # -- exact IDs, one item at a time --------------------------------------- #

    def resolve_ids(self, item: MediaItem, media_type: str) -> None:
        """Read the exact TMDb ID off this one film's page.

        Called only for titles about to be added, so a 600-film watchlist does
        not mean 600 requests - just one per title that actually needs adding.
        """
        if media_type != "movie" or item.ids.get("tmdb") or not item.source_key:
            return

        path = item.source_key if item.source_key.startswith("/") else f"/film/{item.source_key}/"
        try:
            page = self.get_text(f"{BASE}{path}")
        except ProviderError as exc:
            log.debug("Letterboxd film page %s was not readable: %s", path, exc)
            return

        tmdb = TMDB_ON_FILM_PAGE_RE.search(page)
        if tmdb:
            item.ids["tmdb"] = int(tmdb.group(1))
        imdb = IMDB_ON_FILM_PAGE_RE.search(page)
        if imdb:
            item.ids["imdb"] = imdb.group(1)


def _attr(pattern: re.Pattern[str], tag: str) -> str:
    match = pattern.search(tag)
    return html.unescape(match.group(1)) if match else ""


def _split_year(name: str) -> tuple[str, int | None]:
    """"Heat (1995)" -> ("Heat", 1995). Letterboxd always writes it that way."""
    match = YEAR_IN_NAME_RE.search(name)
    if not match:
        return name.strip(), None
    return name[: match.start()].strip(), int(match.group(1))


def _handle(value: str, what: str) -> str:
    """Take the useful part of whatever was typed - a name, or a whole URL."""
    text = str(value or "").strip()
    if "letterboxd.com" in text:
        text = text.split("letterboxd.com", 1)[1]
    parts = [part for part in text.strip("/").split("/") if part and part not in ("list", "film")]
    handle = parts[-1] if parts else ""
    if not handle:
        raise ProviderError(f"This source needs a Letterboxd {what}.")
    return handle
