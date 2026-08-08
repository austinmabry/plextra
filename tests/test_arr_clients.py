"""Exercise the real Radarr/Sonarr clients against a stub *arr server.

The fakes in test_sync.py check the engine's logic; these check the wire format:
URL joining, the API-key header, the add payloads and error handling.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import pytest

from sidecarr.clients import (
    ArrError,
    ArrMetadataError,
    ArrUnknownIdError,
    RadarrClient,
    SonarrClient,
)

RECORDED: dict = {}
REQUESTS: list[str] = []


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
        REQUESTS.append(path)

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
            # Radarr's term search, used to turn an IMDb ID into a TMDb one and,
            # as a last resort, to search a bare title.
            # Radarr's own search is case-insensitive, so the stub is too.
            term = query.get("term", [""])[0]
            if term.lower() == "imdb:tt0133093":
                return self._send(200, [{"tmdbId": 329865, "title": "Arrival"}])
            term = term.title() if ":" not in term else term
            if term == "Heat":
                # Radarr really does return several films called Heat.
                return self._send(200, [
                    {"tmdbId": 111, "title": "Heat", "year": 1972},
                    {"tmdbId": 949, "title": "Heat", "year": 1995},
                    {"tmdbId": 222, "title": "Heat", "year": 2024},
                ])
            if term == "The Matrix":
                return self._send(200, [
                    {
                        "tmdbId": 603,
                        "title": "The Matrix",
                        "originalTitle": "The Matrix",
                        "year": 1999,
                    }
                ])
            if term == "Ambiguous":
                # A near miss: the search answers, but with a different film.
                return self._send(200, [{"tmdbId": 777, "title": "Ambiguous Sequel", "year": 2001}])
            if term == "No Year Anywhere":
                return self._send(200, [{"tmdbId": 888, "title": "Something Else"}])
            return self._send(200, [])

        if path == "/api/v3/movie/lookup/tmdb":
            tmdb_id = int(query["tmdbId"][0])
            if tmdb_id == 404404:
                return self._send(404, {"message": "Movie not found"})
            if tmdb_id == 500500:
                # Radarr surfaces a failure of its own metadata service as a
                # 500. A title it simply does not have gives the 404 above.
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
            if ":" not in term:
                if term == "Severance":
                    return self._send(200, [
                        {"tvdbId": 371980, "title": "Severance", "year": 2022},
                        {"tvdbId": 111, "title": "Severance", "year": 2006},
                    ])
                if term == "Wrong Year":
                    return self._send(200, [{"tvdbId": 222, "title": "Wrong Year", "year": 1999}])
                return self._send(200, [])
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

    def test_an_unknown_id_is_reported_as_unknown(self, radarr):
        """Radarr's metadata service answers a genuine miss with a 404."""
        with pytest.raises(ArrUnknownIdError, match="does not recognise") as exc:
            radarr.add_movie(404404, quality_profile_id=4, root_folder="/movies")
        assert "deleted or merged" in str(exc.value)

    def test_a_server_error_is_not_mistaken_for_an_unknown_title(self, radarr):
        """The distinction that matters.

        A 5xx means Radarr's metadata service, or the network to it, failed -
        the title is very likely fine and worth retrying. Reporting that as
        "this title does not exist" sends people off blacklisting IDs that were
        never the problem.
        """
        with pytest.raises(ArrMetadataError) as exc:
            radarr.add_movie(500500, quality_profile_id=4, root_folder="/movies")

        message = str(exc.value)
        assert "metadata service" in message
        assert "temporary" in message
        assert "does not recognise" not in message

    def test_server_errors_are_still_retried(self, radarr):
        """They are transient, so backing off and trying again is the point."""
        REQUESTS.clear()
        with pytest.raises(ArrMetadataError):
            radarr.lookup_tmdb(500500)
        assert REQUESTS.count("/api/v3/movie/lookup/tmdb") == 3

    def test_falls_back_to_an_imdb_lookup(self, radarr):
        """The two lookups go through different paths in Radarr, so one can
        work when the other does not."""
        assert radarr.resolve_for_add(404404, "tt0133093") is not None

    def test_radarr_error_message_is_surfaced(self, radarr):
        with pytest.raises(ArrError, match="already been added"):
            radarr.add_movie(409409, quality_profile_id=4, root_folder="/movies")


class TestResolveByTitle:
    """The last resort, for sources that publish no IDs at all. A search result
    is a guess, so a near miss must come back as nothing rather than the wrong
    film in someone's library."""

    def test_the_year_picks_the_right_film_of_that_name(self, radarr):
        assert radarr.resolve_by_title("Heat", 1995) == 949

    def test_a_different_year_is_refused_even_though_the_title_matches(self, radarr):
        assert radarr.resolve_by_title("Heat", 1985) is None

    def test_with_no_year_an_exact_title_match_is_accepted(self, radarr):
        assert radarr.resolve_by_title("The Matrix") == 603

    def test_with_no_year_a_near_title_is_refused(self, radarr):
        """"Ambiguous" returning "Ambiguous Sequel" must not count."""
        assert radarr.resolve_by_title("Ambiguous") is None

    def test_a_result_with_no_year_is_refused_when_a_year_was_wanted(self, radarr):
        assert radarr.resolve_by_title("No Year Anywhere", 2001) is None

    def test_no_results_is_not_an_error(self, radarr):
        assert radarr.resolve_by_title("Nothing Like This Exists", 1999) is None

    def test_an_empty_title_never_reaches_the_network(self, radarr):
        REQUESTS.clear()
        assert radarr.resolve_by_title("   ") is None
        assert REQUESTS == []

    def test_case_does_not_matter(self, radarr):
        assert radarr.resolve_by_title("the matrix") == 603


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

    def test_resolves_a_series_by_title_and_year(self, sonarr):
        assert sonarr.resolve_by_title("Severance", 2022) == 371980

    def test_a_wrong_year_is_refused(self, sonarr):
        assert sonarr.resolve_by_title("Wrong Year", 2020) is None

    def test_with_no_year_an_exact_title_is_needed(self, sonarr):
        assert sonarr.resolve_by_title("Wrong Year") == 222
