"""Exercise the real Radarr/Sonarr clients against a stub *arr server.

The fakes in test_sync.py check the engine's logic; these check the wire format:
URL joining, the API-key header, the add payloads and error handling.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import pytest

from plextra.clients import ArrError, RadarrClient, SonarrClient

RECORDED: dict = {}


class StubArrHandler(BaseHTTPRequestHandler):
    """Serves just enough of the Radarr/Sonarr v3 API."""

    def log_message(self, *args):
        pass

    # -- helpers ------------------------------------------------------------ #

    def _send(self, status, payload):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _unauthorized(self):
        if self.headers.get("X-Api-Key") != "test-key":
            self._send(401, {"message": "Unauthorized"})
            return True
        return False

    # -- routes ------------------------------------------------------------- #

    def do_GET(self):
        if self._unauthorized():
            return
        url = urlparse(self.path)
        query = parse_qs(url.query)
        path = url.path

        routes = {
            "/api/v3/system/status": (200, {"version": "5.2.6.8376", "appName": "Radarr"}),
            "/api/v3/qualityprofile": (200, [{"id": 4, "name": "HD-1080p"}]),
            "/api/v3/rootfolder": (200, [{"id": 1, "path": "/movies", "freeSpace": 100}]),
            "/api/v3/tag": (200, [{"id": 7, "label": "trakt"}]),
            "/api/v3/movie": (200, [
                {"tmdbId": 603, "title": "The Matrix", "imdbId": "tt0133093"},
                # A library entry with no TMDb ID must not break the sweep.
                {"title": "Mystery", "imdbId": "tt9999999"},
            ]),
            "/api/v3/exclusions": (200, [{"tmdbId": 11, "movieTitle": "Nope"}]),
            "/api/v3/series": (200, [{"tvdbId": 121361, "title": "Game of Thrones"}]),
            "/api/v3/importlistexclusion": (200, [{"tvdbId": 22, "title": "Nope"}]),
            # Simulate Sonarr v4, which removed language profiles.
            "/api/v3/languageprofile": (404, {"message": "Not Found"}),
        }
        if path in routes:
            return self._send(*routes[path])

        if path == "/api/v3/movie/lookup":
            # Radarr's term search, used to turn an IMDb ID into a TMDb one.
            term = query.get("term", [""])[0]
            if term == "imdb:tt0133093":
                return self._send(200, [{"tmdbId": 329865, "title": "Arrival"}])
            return self._send(200, [])

        if path == "/api/v3/movie/lookup/tmdb":
            tmdb_id = int(query["tmdbId"][0])
            if tmdb_id == 404404:
                return self._send(404, {"message": "Movie not found"})
            if tmdb_id == 500500:
                # What Radarr actually returns when its metadata server has no
                # entry for the ID: a 500, not a 404.
                return self._send(500, {"message": "Internal Server Error"})
            return self._send(200, {
                "id": 0,
                "title": "Arrival",
                "titleSlug": "arrival-329865",
                "tmdbId": tmdb_id,
                "year": 2016,
                "images": [{"coverType": "poster"}],
                "monitored": False,
            })

        if path == "/api/v3/series/lookup":
            term = query["term"][0]
            tvdb_id = int(term.split(":")[1])
            if tvdb_id == 404404:
                return self._send(200, [])
            return self._send(200, [{
                "id": 0,
                "title": "Severance",
                "titleSlug": "severance",
                "tvdbId": tvdb_id,
                "year": 2022,
                "seasons": [{"seasonNumber": 1, "monitored": True}],
            }])

        self._send(404, {"message": "Not Found"})

    def do_POST(self):
        if self._unauthorized():
            return
        length = int(self.headers.get("Content-Length", 0))
        payload = json.loads(self.rfile.read(length) or b"{}")
        path = urlparse(self.path).path
        RECORDED[path] = payload

        if path == "/api/v3/movie":
            if payload.get("tmdbId") == 409409:
                return self._send(400, [{"errorMessage": "This movie has already been added"}])
            return self._send(201, {**payload, "id": 42})

        if path == "/api/v3/series":
            return self._send(201, {**payload, "id": 43})

        self._send(404, {"message": "Not Found"})


@pytest.fixture(scope="module")
def stub_url():
    server = ThreadingHTTPServer(("127.0.0.1", 0), StubArrHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()


@pytest.fixture
def radarr(stub_url):
    RECORDED.clear()
    with RadarrClient(stub_url, "test-key") as client:
        yield client


@pytest.fixture
def sonarr(stub_url):
    RECORDED.clear()
    with SonarrClient(stub_url, "test-key") as client:
        yield client


class TestConstruction:
    def test_missing_url_rejected(self):
        with pytest.raises(ArrError, match="URL"):
            RadarrClient("", "key")

    def test_missing_api_key_rejected(self, stub_url):
        with pytest.raises(ArrError, match="API key"):
            RadarrClient(stub_url, "")

    def test_trailing_slash_is_harmless(self, stub_url):
        with RadarrClient(stub_url + "/", "test-key") as client:
            assert client.version() == "5.2.6.8376"

    def test_wrong_api_key_is_reported(self, stub_url):
        with RadarrClient(stub_url, "wrong") as client:
            with pytest.raises(ArrError, match="rejected the API key"):
                client.system_status()

    def test_unreachable_host_fails_fast_with_retries_disabled(self):
        """The Test button must not make the settings page hang on retries."""
        import time

        start = time.monotonic()
        with RadarrClient("http://127.0.0.1:9", "key", timeout=2, retries=1) as client:
            with pytest.raises(ArrError):
                client.system_status()
        assert time.monotonic() - start < 5


class TestRadarr:
    def test_test_bundles_everything_the_ui_needs(self, radarr):
        result = radarr.test()
        assert result["ok"] is True
        assert result["version"] == "5.2.6.8376"
        assert result["quality_profiles"] == [{"id": 4, "name": "HD-1080p"}]
        assert result["root_folders"][0]["path"] == "/movies"
        assert result["tags"] == [{"id": 7, "label": "trakt"}]

    def test_library_ids(self, radarr):
        assert radarr.library_tmdb_ids() == {603}

    def test_library_ids_returns_imdb_ids_too(self, radarr):
        """Needed to spot a film already held under a different TMDb ID."""
        tmdb_ids, imdb_ids = radarr.library_ids()
        assert tmdb_ids == {603}
        assert imdb_ids == {"tt0133093", "tt9999999"}

    def test_exclusion_ids(self, radarr):
        assert radarr.exclusion_tmdb_ids() == {11}

    def test_lookup(self, radarr):
        assert radarr.lookup_tmdb(329865)["titleSlug"] == "arrival-329865"

    def test_lookup_miss_returns_none(self, radarr):
        assert radarr.lookup_tmdb(404404) is None

    def test_add_payload(self, radarr):
        radarr.add_movie(
            329865,
            quality_profile_id=4,
            root_folder="/movies",
            minimum_availability="inCinemas",
            monitored=True,
            search_on_add=True,
            tags=[7, 7, 3],
        )
        payload = RECORDED["/api/v3/movie"]

        assert payload["tmdbId"] == 329865
        assert payload["titleSlug"] == "arrival-329865"
        assert payload["qualityProfileId"] == 4
        assert payload["rootFolderPath"] == "/movies"
        assert payload["minimumAvailability"] == "inCinemas"
        assert payload["monitored"] is True
        assert payload["addOptions"]["searchForMovie"] is True
        assert payload["tags"] == [3, 7]
        # A lookup result carries id 0; sending it makes Radarr treat the POST
        # as an update to a movie that does not exist.
        assert "id" not in payload

    def test_unresolvable_id_explains_why(self, radarr):
        """Radarr answers a metadata miss with a 500; say what that means."""
        with pytest.raises(ArrError, match="has no metadata") as exc:
            radarr.add_movie(404404, quality_profile_id=4, root_folder="/movies")
        assert "cancelled, unreleased" in str(exc.value)

    def test_a_metadata_miss_is_not_retried_to_death(self, radarr, stub_url):
        """A 500 here is deterministic, so three tries with backoff is six
        wasted seconds per title."""
        import time

        start = time.monotonic()
        assert radarr.lookup_tmdb(500500) is None
        assert time.monotonic() - start < 4

    def test_falls_back_to_an_imdb_lookup(self, radarr):
        """The two lookups go through different paths in Radarr, so one can
        work when the other does not."""
        assert radarr.resolve_for_add(404404, "tt0133093") is not None

    def test_radarr_error_message_is_surfaced(self, radarr):
        with pytest.raises(ArrError, match="already been added"):
            radarr.add_movie(409409, quality_profile_id=4, root_folder="/movies")


class TestSonarr:
    def test_library_ids(self, sonarr):
        assert sonarr.library_tvdb_ids() == {121361}

    def test_exclusion_ids(self, sonarr):
        assert sonarr.exclusion_tvdb_ids() == {22}

    def test_v4_has_no_language_profiles(self, sonarr):
        assert sonarr.language_profiles() == []
        assert sonarr.supports_language_profiles() is False

    def test_lookup(self, sonarr):
        assert sonarr.lookup_tvdb(371980)["titleSlug"] == "severance"

    def test_lookup_miss_returns_none(self, sonarr):
        assert sonarr.lookup_tvdb(404404) is None

    def test_add_payload(self, sonarr):
        sonarr.add_series(
            371980,
            quality_profile_id=5,
            root_folder="/tv",
            monitor="firstSeason",
            monitored=True,
            season_folder=True,
            series_type="anime",
            search_on_add=True,
            tags=[7],
        )
        payload = RECORDED["/api/v3/series"]

        assert payload["tvdbId"] == 371980
        assert payload["titleSlug"] == "severance"
        assert payload["qualityProfileId"] == 5
        assert payload["rootFolderPath"] == "/tv"
        assert payload["seasonFolder"] is True
        assert payload["seriesType"] == "anime"
        assert payload["addOptions"]["monitor"] == "firstSeason"
        assert payload["addOptions"]["searchForMissingEpisodes"] is True
        assert "languageProfileId" not in payload
        assert "id" not in payload

    def test_language_profile_included_when_asked(self, sonarr):
        sonarr.add_series(
            371980, quality_profile_id=5, root_folder="/tv", language_profile_id=1
        )
        assert RECORDED["/api/v3/series"]["languageProfileId"] == 1
