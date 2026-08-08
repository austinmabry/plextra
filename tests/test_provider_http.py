"""Providers against a stub server: request shape, auth, pagination.

test_providers.py checks the payload -> MediaItem conversion. This checks the
half above it - that each provider hits the right path with the right
parameters and walks pages correctly.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import pytest

from sidecarr import providers
from sidecarr.config import AppConfig, Source
from sidecarr.providers.base import ProviderAuthError, ProviderError

REQUESTS: list[tuple[str, dict]] = []


class StubHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, status, payload, raw=False):
        body = payload.encode() if raw else json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/plain" if raw else "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        url = urlparse(self.path)
        query = {k: v[0] for k, v in parse_qs(url.query).items()}
        REQUESTS.append((url.path, query))
        path = url.path

        # ---- TMDb ----------------------------------------------------------
        if path == "/3/genre/movie/list":
            return self._send(200, {"genres": [{"id": 18, "name": "Drama"}]})
        if path == "/3/genre/tv/list":
            return self._send(200, {"genres": []})
        if path == "/3/movie/popular":
            if query.get("api_key") != "tmdb-key":
                return self._send(401, {"status_message": "Invalid API key"})
            page = int(query.get("page", 1))
            if page > 2:
                return self._send(200, {"results": [], "total_pages": 2})
            return self._send(200, {
                "page": page,
                "total_pages": 2,
                "results": [
                    {"id": 100 + page, "title": f"Movie {page}",
                     "release_date": "2020-01-01", "genre_ids": [18]}
                ],
            })
        if path == "/3/tv/95396/external_ids":
            return self._send(200, {"tvdb_id": 371980, "imdb_id": "tt11280740"})
        if path == "/3/discover/movie":
            return self._send(200, {"results": [{"id": 7, "title": "Discovered"}], "total_pages": 1})
        if path == "/3/configuration":
            return self._send(200, {"images": {}})

        # ---- MDBList -------------------------------------------------------
        if path.startswith("/lists/") and path.endswith("/items"):
            if query.get("apikey") != "mdb-key":
                return self._send(401, {"error": "bad key"})
            return self._send(200, {
                "movies": [{"title": "MDB Movie", "imdb_id": "tt1", "release_year": 2019}],
                "shows": [{"title": "MDB Show", "tvdb_id": 42, "release_year": 2021}],
            })
        if path == "/lists/user":
            return self._send(200, [{"id": 9, "name": "My list", "items": 12}])
        if path == "/user":
            return self._send(200, {"username": "someone"})

        # ---- Plex ----------------------------------------------------------
        if path == "/library/sections/watchlist/all":
            if query.get("X-Plex-Token") != "plex-token":
                return self._send(401, {"error": "bad token"})
            wanted = query.get("type")
            entry = {
                "type": "movie" if wanted == "1" else "show",
                "title": "Watchlisted",
                "year": 2018,
                "Guid": [{"id": "tmdb://500"}, {"id": "tvdb://600"}],
            }
            return self._send(200, {"MediaContainer": {"totalSize": 1, "size": 1,
                                                       "Metadata": [entry]}})

        # ---- plain URL sources ---------------------------------------------
        if path == "/stevenlu.json":
            return self._send(200, [{"title": "Popular", "imdb_id": "tt0133093"}])
        if path == "/custom.json":
            return self._send(200, [{"title": "Custom", "tvdbId": 77}])
        if path == "/custom.xml":
            return self._send(200, raw=True, payload=(
                '<rss><channel><item><title>Feed Movie</title>'
                '<guid>tt0111161</guid></item></channel></rss>'
            ))
        if path == "/empty.json":
            return self._send(200, [])
        if path == "/private.json":
            return self._send(403, {"error": "nope"})

        self._send(404, {"error": "not found"})


@pytest.fixture(scope="module")
def stub():
    server = ThreadingHTTPServer(("127.0.0.1", 0), StubHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()


@pytest.fixture
def config():
    return AppConfig()


@pytest.fixture(autouse=True)
def clear_requests():
    REQUESTS.clear()


def source(provider, type_, **options):
    return Source(provider=provider, type=type_, options={k: str(v) for k, v in options.items()})


class TestTmdb:
    @pytest.fixture
    def provider(self, config, stub, monkeypatch):
        monkeypatch.setattr("sidecarr.providers.tmdb.BASE", f"{stub}/3")
        config.tmdb.api_key = "tmdb-key"
        p = providers.build("tmdb", config)
        yield p
        p.close()

    def test_popular_walks_every_page(self, provider):
        items = provider.fetch(source("tmdb", "popular"), "movie")
        assert [i.title for i in items] == ["Movie 1", "Movie 2"]

    def test_genre_ids_are_mapped_to_names(self, provider):
        assert provider.fetch(source("tmdb", "popular"), "movie")[0].genres == ["drama"]

    def test_api_key_is_sent(self, provider):
        provider.fetch(source("tmdb", "popular"), "movie")
        assert all(q.get("api_key") == "tmdb-key" for _, q in REQUESTS)

    def test_a_bad_key_is_reported_as_auth(self, config, stub, monkeypatch):
        monkeypatch.setattr("sidecarr.providers.tmdb.BASE", f"{stub}/3")
        config.tmdb.api_key = "wrong"
        with providers.build("tmdb", config) as provider:
            with pytest.raises(ProviderAuthError):
                provider.fetch(source("tmdb", "popular"), "movie")

    def test_missing_key_never_reaches_the_network(self, config, stub, monkeypatch):
        monkeypatch.setattr("sidecarr.providers.tmdb.BASE", f"{stub}/3")
        p = providers.build("tmdb", config)
        try:
            with pytest.raises(ProviderAuthError, match="No TMDb API key"):
                p.fetch(source("tmdb", "popular"), "movie")
        finally:
            p.close()
        assert REQUESTS == []

    def test_limit_bounds_how_many_pages_are_pulled(self, provider):
        provider.fetch(source("tmdb", "popular"), "movie", max_items=1)
        pages = [q.get("page") for path, q in REQUESTS if path == "/3/movie/popular"]
        assert pages == ["1"]

    def test_company_uses_discover(self, provider):
        items = provider.fetch(source("tmdb", "company", company_id="420"), "movie")
        assert [i.title for i in items] == ["Discovered"]
        path, query = next(r for r in REQUESTS if r[0] == "/3/discover/movie")
        assert query["with_companies"] == "420"

    def test_show_ids_are_resolved_to_tvdb(self, provider):
        from sidecarr.providers.base import MediaItem

        item = MediaItem(ids={"tmdb": 95396})
        provider.resolve_ids(item, "show")
        assert item.ids["tvdb"] == 371980
        assert item.ids["imdb"] == "tt11280740"


class TestMdblist:
    @pytest.fixture
    def provider(self, config, stub, monkeypatch):
        monkeypatch.setattr("sidecarr.providers.mdblist.BASE", stub)
        config.mdblist.api_key = "mdb-key"
        p = providers.build("mdblist", config)
        yield p
        p.close()

    def test_fetches_a_list_by_slug(self, provider):
        items = provider.fetch(source("mdblist", "list", list_url="someone/faves"), "movie")
        assert [i.title for i in items] == ["MDB Movie"]
        assert REQUESTS[0][0] == "/lists/someone/faves/items"

    def test_fetches_a_list_by_id(self, provider):
        provider.fetch(source("mdblist", "list", list_url="12345"), "movie")
        assert REQUESTS[0][0] == "/lists/12345/items"

    def test_shows_come_from_the_shows_bucket(self, provider):
        items = provider.fetch(source("mdblist", "list", list_url="a/b"), "show")
        assert [i.title for i in items] == ["MDB Show"]

    def test_api_key_is_sent(self, provider):
        provider.fetch(source("mdblist", "list", list_url="a/b"), "movie")
        assert REQUESTS[0][1]["apikey"] == "mdb-key"

    def test_my_lists_for_the_picker(self, provider):
        assert provider.my_lists() == [
            {"name": "My list", "url": "9", "item_count": 12, "owner": "", "owned": True}
        ]

    def test_bad_key_is_auth_error(self, config, stub, monkeypatch):
        monkeypatch.setattr("sidecarr.providers.mdblist.BASE", stub)
        config.mdblist.api_key = "wrong"
        p = providers.build("mdblist", config)
        try:
            with pytest.raises(ProviderAuthError):
                p.fetch(source("mdblist", "list", list_url="a/b"), "movie")
        finally:
            p.close()


class TestPlex:
    @pytest.fixture
    def provider(self, config, stub, monkeypatch):
        monkeypatch.setattr("sidecarr.providers.plex.DISCOVER", stub)
        config.plex.token = "plex-token"
        p = providers.build("plex", config)
        yield p
        p.close()

    def test_fetches_the_watchlist(self, provider):
        items = provider.fetch(source("plex", "watchlist"), "movie")
        assert [i.title for i in items] == ["Watchlisted"]
        assert items[0].ids == {"tmdb": 500, "tvdb": 600}

    def test_movies_and_shows_use_different_type_codes(self, provider):
        provider.fetch(source("plex", "watchlist"), "movie")
        assert REQUESTS[-1][1]["type"] == "1"
        REQUESTS.clear()
        provider.fetch(source("plex", "watchlist"), "show")
        assert REQUESTS[-1][1]["type"] == "2"

    def test_guids_are_requested(self, provider):
        provider.fetch(source("plex", "watchlist"), "movie")
        assert REQUESTS[-1][1]["includeGuids"] == "1"

    def test_bad_token_is_auth_error(self, config, stub, monkeypatch):
        monkeypatch.setattr("sidecarr.providers.plex.DISCOVER", stub)
        config.plex.token = "wrong"
        p = providers.build("plex", config)
        try:
            with pytest.raises(ProviderAuthError):
                p.fetch(source("plex", "watchlist"), "movie")
        finally:
            p.close()


class TestStevenLu:
    def test_fetches_the_published_list(self, config, stub, monkeypatch):
        monkeypatch.setattr("sidecarr.providers.simple.STEVENLU_URL", f"{stub}/stevenlu.json")
        p = providers.build("stevenlu", config)
        try:
            items = p.fetch(source("stevenlu", "popular"), "movie")
        finally:
            p.close()
        assert [i.ids for i in items] == [{"imdb": "tt0133093"}]


class TestCustom:
    @pytest.fixture
    def provider(self, config):
        p = providers.build("custom", config)
        yield p
        p.close()

    def test_json_list(self, provider, stub):
        items = provider.fetch(source("custom", "url", url=f"{stub}/custom.json"), "show")
        assert [i.ids for i in items] == [{"tvdb": 77}]

    def test_rss_feed(self, provider, stub):
        items = provider.fetch(source("custom", "url", url=f"{stub}/custom.xml"), "movie")
        assert [i.ids for i in items] == [{"imdb": "tt0111161"}]

    def test_empty_list_is_reported_helpfully(self, provider, stub):
        with pytest.raises(ProviderError, match="Nothing usable"):
            provider.fetch(source("custom", "url", url=f"{stub}/empty.json"), "movie")

    def test_forbidden_url_is_reported(self, provider, stub):
        with pytest.raises(ProviderAuthError):
            provider.fetch(source("custom", "url", url=f"{stub}/private.json"), "movie")

    def test_missing_page_is_reported(self, provider, stub):
        with pytest.raises(ProviderError, match="could not find"):
            provider.fetch(source("custom", "url", url=f"{stub}/nope.json"), "movie")
