"""Letterboxd, against a stub serving the markup the real site returns.

The fixtures below are trimmed copies of live pages, so a markup change on
Letterboxd's side shows up here as a failure rather than as an empty list on
someone's dashboard.
"""

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

import pytest

from sidecarr import providers
from sidecarr.config import AppConfig, Source
from sidecarr.providers.base import MediaItem, ProviderError

REQUESTS: list[str] = []


def film_tag(name, slug):
    """The real shape: one react-component div carrying the poster metadata."""
    return (
        '<div class="react-component" data-component-class="LazyPoster" '
        'data-image-width="125" '
        f'data-item-name="{name}" data-item-slug="{slug}" '
        f'data-item-link="/film/{slug}/" data-item-full-display-name="{name}">'
        "</div>"
    )


def grid(*films):
    return "<html><body><ul>" + "".join(film_tag(n, s) for n, s in films) + "</ul></body></html>"


PAGES = {
    # A two-page list, then an empty third page - how Letterboxd really behaves.
    "/dave/list/best-of-2026/": grid(("Heat (1995)", "heat"), ("The Matrix (1999)", "the-matrix")),
    "/dave/list/best-of-2026/page/2/": grid(("Arrival (2016)", "arrival")),
    "/dave/list/best-of-2026/page/3/": grid(),
    "/dave/watchlist/": grid(("Clarissa (2026)", "clarissa-2026")),
    "/dave/watchlist/page/2/": grid(),
    "/dave/films/": grid(("Obsession (2025)", "obsession-2025")),
    "/dave/films/page/2/": grid(),
    # A title with an ampersand and a quote, entity-encoded as the site does.
    "/dave/list/punctuation/": grid(("Fear &amp; Desire (1953)", "fear-and-desire")),
    "/dave/list/punctuation/page/2/": grid(),
    # A film with no year in the name.
    "/dave/list/noyear/": grid(("Untitled Project", "untitled-project")),
    "/dave/list/noyear/page/2/": grid(),
    # A page that parses but holds nothing recognisable.
    "/dave/list/changed-markup/": "<html><body><p>Nothing here</p></body></html>",
    # Individual film pages, where the real IDs live.
    "/film/heat/": '<a href="https://www.themoviedb.org/movie/949/">TMDb</a>'
                   '<a href="http://www.imdb.com/title/tt0113277/maindetails">IMDb</a>',
    "/film/the-matrix/": '<a href="https://www.themoviedb.org/movie/603/">TMDb</a>',
    "/film/arrival/": "<p>No links on this one</p>",
}


class StubHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        path = urlparse(self.path).path
        REQUESTS.append(path)
        if path == "/private/watchlist/":
            return self._send(403, "Forbidden")
        if path not in PAGES:
            return self._send(404, "Not found")
        self._send(200, PAGES[path])

    def _send(self, status, body):
        raw = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


@pytest.fixture(scope="module")
def stub():
    server = ThreadingHTTPServer(("127.0.0.1", 0), StubHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()


@pytest.fixture
def provider(stub, monkeypatch):
    monkeypatch.setattr("sidecarr.providers.letterboxd.BASE", stub)
    REQUESTS.clear()
    p = providers.build("letterboxd", AppConfig())
    yield p
    p.close()


def source(type_, **options):
    return Source(
        provider="letterboxd", type=type_, options={k: str(v) for k, v in options.items()}
    )


class TestParsing:
    def test_a_list_yields_title_and_year(self, provider):
        items = provider.fetch(source("list", username="dave", list_slug="best-of-2026"), "movie")
        assert [(i.title, i.year) for i in items[:2]] == [("Heat", 1995), ("The Matrix", 1999)]

    def test_entities_in_a_title_are_decoded(self, provider):
        items = provider.fetch(source("list", username="dave", list_slug="punctuation"), "movie")
        assert items[0].title == "Fear & Desire"

    def test_a_title_with_no_year_still_comes_through(self, provider):
        items = provider.fetch(source("list", username="dave", list_slug="noyear"), "movie")
        assert (items[0].title, items[0].year) == ("Untitled Project", None)

    def test_the_film_link_is_kept_for_the_id_lookup(self, provider):
        items = provider.fetch(source("watchlist", username="dave"), "movie")
        assert items[0].source_key == "/film/clarissa-2026/"

    def test_no_ids_come_from_a_list_page(self, provider):
        """Letterboxd list pages carry no IDs at all - that is the whole problem."""
        items = provider.fetch(source("watchlist", username="dave"), "movie")
        assert items[0].ids == {}


class TestPagination:
    def test_every_page_is_walked(self, provider):
        items = provider.fetch(source("list", username="dave", list_slug="best-of-2026"), "movie")
        assert [i.title for i in items] == ["Heat", "The Matrix", "Arrival"]

    def test_it_stops_at_the_first_empty_page(self, provider):
        provider.fetch(source("list", username="dave", list_slug="best-of-2026"), "movie")
        assert "/dave/list/best-of-2026/page/4/" not in REQUESTS

    def test_a_limit_bounds_the_walk(self, provider):
        provider.fetch(source("watchlist", username="dave"), "movie", max_items=1)
        assert REQUESTS.count("/dave/watchlist/") == 1


class TestSourceTypes:
    def test_watchlist_path(self, provider):
        provider.fetch(source("watchlist", username="dave"), "movie")
        assert REQUESTS[0] == "/dave/watchlist/"

    def test_films_path(self, provider):
        provider.fetch(source("films", username="dave"), "movie")
        assert REQUESTS[0] == "/dave/films/"

    def test_a_pasted_list_url_is_understood(self, provider):
        items = provider.fetch(
            source("list", username="", list_slug="https://letterboxd.com/dave/list/best-of-2026/"),
            "movie",
        )
        assert len(items) == 3

    def test_a_list_url_pasted_into_the_username_works_too(self, provider):
        items = provider.fetch(
            source("list", username="letterboxd.com/dave/list/best-of-2026/", list_slug=""), "movie"
        )
        assert len(items) == 3

    def test_a_profile_url_instead_of_a_username(self, provider):
        provider.fetch(source("watchlist", username="https://letterboxd.com/dave/"), "movie")
        assert REQUESTS[0] == "/dave/watchlist/"

    def test_shows_are_refused_because_letterboxd_has_none(self, provider):
        with pytest.raises(ProviderError, match="only tracks films"):
            provider.fetch(source("watchlist", username="dave"), "show")

    def test_only_movie_sources_are_advertised(self):
        described = providers.build("letterboxd", AppConfig()).describe()
        assert described["media"] == ["movie"]


class TestExactIds:
    def test_the_film_page_gives_the_tmdb_id(self, provider):
        item = MediaItem(title="Heat", year=1995, source_key="/film/heat/")
        provider.resolve_ids(item, "movie")
        assert item.ids == {"tmdb": 949, "imdb": "tt0113277"}

    def test_a_bare_slug_works_as_well_as_a_path(self, provider):
        item = MediaItem(title="Heat", source_key="heat")
        provider.resolve_ids(item, "movie")
        assert item.ids["tmdb"] == 949

    def test_a_film_page_without_links_is_left_alone(self, provider):
        """Radarr's title search is the next fallback, so this is not fatal."""
        item = MediaItem(title="Arrival", year=2016, source_key="/film/arrival/")
        provider.resolve_ids(item, "movie")
        assert item.ids == {}

    def test_an_unreachable_film_page_does_not_raise(self, provider):
        item = MediaItem(title="Gone", source_key="/film/missing/")
        provider.resolve_ids(item, "movie")
        assert item.ids == {}

    def test_an_item_that_already_has_an_id_costs_no_request(self, provider):
        item = MediaItem(title="Heat", ids={"tmdb": 949}, source_key="/film/heat/")
        provider.resolve_ids(item, "movie")
        assert REQUESTS == []

    def test_the_whole_list_is_not_resolved_up_front(self, provider):
        """The point of doing this lazily: a 600-film watchlist is not 600 requests."""
        provider.fetch(source("list", username="dave", list_slug="best-of-2026"), "movie")
        assert not [path for path in REQUESTS if path.startswith("/film/")]


class TestFailures:
    def test_a_forbidden_page_explains_privacy(self, provider):
        with pytest.raises(ProviderError, match="Private profiles"):
            provider.fetch(source("watchlist", username="private"), "movie")

    def test_a_missing_profile_says_to_check_the_name(self, provider):
        with pytest.raises(ProviderError, match="Check the username"):
            provider.fetch(source("watchlist", username="nobody"), "movie")

    def test_a_page_with_no_films_is_not_reported_as_an_empty_list(self, provider):
        with pytest.raises(ProviderError, match="No films found"):
            provider.fetch(source("list", username="dave", list_slug="changed-markup"), "movie")

    def test_a_missing_username_is_caught_before_any_request(self, provider):
        with pytest.raises(ProviderError, match="needs a Letterboxd username"):
            provider.fetch(source("watchlist", username=""), "movie")
        assert REQUESTS == []

    def test_a_list_with_no_slug_is_caught(self, provider):
        with pytest.raises(ProviderError, match="needs a Letterboxd list"):
            provider.fetch(source("list", username="dave", list_slug=""), "movie")


class TestRegistration:
    def test_it_is_in_the_registry(self):
        assert "letterboxd" in providers.PROVIDERS

    def test_it_needs_no_credentials(self):
        assert providers.build("letterboxd", AppConfig()).configured() is True

    def test_the_blurb_warns_that_the_terms_forbid_this(self):
        """Someone picking this source should not have to read the README first."""
        blurb = providers.build("letterboxd", AppConfig()).blurb.lower()
        assert "terms prohibit" in blurb
        assert "paste or file" in blurb

    def test_the_sanctioned_route_is_named_in_the_editor(self):
        described = providers.build("letterboxd", AppConfig()).describe()
        watchlist = next(s for s in described["sources"] if s["key"] == "watchlist")
        assert "Import & Export" in watchlist["help"]
