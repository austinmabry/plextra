import pytest

from plextra.clients import ArrError
from plextra.config import Filters, ListJob, Source
from plextra.sync import SyncConfigError, SyncEngine


def movie(tmdb, title, **overrides):
    base = {
        "title": title,
        "year": 2016,
        "ids": {"trakt": tmdb, "tmdb": tmdb},
        "runtime": 120,
        "country": "us",
        "language": "en",
        "genres": ["drama"],
        "rating": 7.5,
        "votes": 1000,
    }
    base.update(overrides)
    return base


def tvshow(tvdb, title, **overrides):
    base = {
        "title": title,
        "year": 2022,
        "ids": {"trakt": tvdb, "tvdb": tvdb},
        "runtime": 50,
        "country": "us",
        "language": "en",
        "network": "HBO",
        "genres": ["drama"],
        "rating": 8.0,
        "votes": 500,
    }
    base.update(overrides)
    return base


@pytest.fixture
def trakt_items(monkeypatch):
    holder = {"items": []}

    class FakeTrakt:
        def __init__(self, cfg, on_account_update=None, timeout=30.0):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def fetch(self, source, media_type, max_items=0):
            return list(holder["items"])

    monkeypatch.setattr("plextra.sync.TraktClient", FakeTrakt)
    return holder


@pytest.fixture
def radarr(monkeypatch):
    state = {"library": set(), "exclusions": set(), "added": [], "fail": set()}

    class FakeRadarr:
        def __init__(self, url, api_key):
            state["url"] = url

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def library_tmdb_ids(self):
            return set(state["library"])

        def exclusion_tmdb_ids(self):
            return set(state["exclusions"])

        def add_movie(self, tmdb_id, **kwargs):
            if tmdb_id in state["fail"]:
                raise ArrError("Radarr said no")
            state["added"].append((tmdb_id, kwargs))
            return {"id": 1}

    monkeypatch.setattr("plextra.sync.RadarrClient", FakeRadarr)
    return state


@pytest.fixture
def sonarr(monkeypatch):
    state = {"library": set(), "exclusions": set(), "added": [], "languages": True}

    class FakeSonarr:
        def __init__(self, url, api_key):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def library_tvdb_ids(self):
            return set(state["library"])

        def exclusion_tvdb_ids(self):
            return set(state["exclusions"])

        def supports_language_profiles(self):
            return state["languages"]

        def add_series(self, tvdb_id, **kwargs):
            state["added"].append((tvdb_id, kwargs))
            return {"id": 1}

    monkeypatch.setattr("plextra.sync.SonarrClient", FakeSonarr)
    return state


@pytest.fixture
def engine(store, database):
    def configure(config):
        config.trakt.client_id = "abc"
        config.trakt.client_secret = "def"
        config.radarr.enabled = True
        config.radarr.url = "http://radarr:7878"
        config.radarr.api_key = "radarr-key"
        config.radarr.quality_profile_id = 4
        config.radarr.root_folder = "/movies"
        config.sonarr.enabled = True
        config.sonarr.url = "http://sonarr:8989"
        config.sonarr.api_key = "sonarr-key"
        config.sonarr.quality_profile_id = 5
        config.sonarr.root_folder = "/tv"

    store.mutate(configure)
    return SyncEngine(store, database)


def add_list(store, **kwargs):
    job = ListJob(**kwargs)
    store.mutate(lambda config: config.lists.append(job))
    return job


class TestHappyPath:
    def test_adds_new_movies(self, engine, store, trakt_items, radarr):
        trakt_items["items"] = [movie(1, "One"), movie(2, "Two")]
        job = add_list(store, name="Watchlist")

        result = engine.run(job.id)

        assert result.status == "success"
        assert result.added == 2
        assert [tmdb for tmdb, _ in radarr["added"]] == [1, 2]

    def test_passes_target_settings_through(self, engine, store, trakt_items, radarr):
        trakt_items["items"] = [movie(1, "One")]
        store.mutate(lambda c: setattr(c.radarr, "tags", [7]))
        store.mutate(lambda c: setattr(c.radarr, "search_on_add", True))
        job = add_list(store, name="Watchlist")

        engine.run(job.id)

        _, kwargs = radarr["added"][0]
        assert kwargs["quality_profile_id"] == 4
        assert kwargs["root_folder"] == "/movies"
        assert kwargs["tags"] == [7]
        assert kwargs["search_on_add"] is True
        assert kwargs["minimum_availability"] == "released"

    def test_per_list_overrides_win(self, engine, store, trakt_items, radarr):
        trakt_items["items"] = [movie(1, "One")]
        job = add_list(
            store,
            name="4K",
            quality_profile_id=9,
            root_folder="/movies4k",
            tags=[3],
            search_on_add=False,
        )

        engine.run(job.id)

        _, kwargs = radarr["added"][0]
        assert kwargs["quality_profile_id"] == 9
        assert kwargs["root_folder"] == "/movies4k"
        assert kwargs["tags"] == [3]
        assert kwargs["search_on_add"] is False

    def test_adds_shows_via_sonarr(self, engine, store, trakt_items, sonarr):
        trakt_items["items"] = [tvshow(101, "Show")]
        job = add_list(store, name="Shows", media_type="show")

        result = engine.run(job.id)

        assert result.added == 1
        tvdb, kwargs = sonarr["added"][0]
        assert tvdb == 101
        assert kwargs["monitor"] == "all"
        assert kwargs["season_folder"] is True


class TestSkipping:
    def test_skips_titles_already_in_the_library(self, engine, store, trakt_items, radarr):
        trakt_items["items"] = [movie(1, "Have it"), movie(2, "Want it")]
        radarr["library"] = {1}
        job = add_list(store, name="Watchlist")

        result = engine.run(job.id)

        assert result.existing == 1
        assert [tmdb for tmdb, _ in radarr["added"]] == [2]

    def test_respects_radarr_exclusions(self, engine, store, trakt_items, radarr):
        trakt_items["items"] = [movie(1, "Excluded"), movie(2, "Fine")]
        radarr["exclusions"] = {1}
        job = add_list(store, name="Watchlist")

        result = engine.run(job.id)

        assert result.excluded == 1
        assert [tmdb for tmdb, _ in radarr["added"]] == [2]

    def test_skips_items_without_a_tmdb_id(self, engine, store, trakt_items, radarr):
        trakt_items["items"] = [{"title": "No ID", "year": 2020, "ids": {"trakt": 5}}]
        job = add_list(store, name="Watchlist")

        result = engine.run(job.id)

        assert result.added == 0
        assert radarr["added"] == []

    def test_applies_filters(self, engine, store, trakt_items, radarr):
        trakt_items["items"] = [movie(1, "Old", year=1980), movie(2, "New", year=2020)]
        job = add_list(store, name="Watchlist", filters=Filters(min_year=2000))

        result = engine.run(job.id)

        assert result.filtered == 1
        assert [tmdb for tmdb, _ in radarr["added"]] == [2]


class TestLimitAndSort:
    def test_limit_applies_after_filtering(self, engine, store, trakt_items, radarr):
        """Ask for 2 and you get 2 that passed, not 2 candidates that may fail."""
        trakt_items["items"] = [
            movie(1, "Old", year=1980),
            movie(2, "New A", year=2020),
            movie(3, "New B", year=2021),
            movie(4, "New C", year=2022),
        ]
        job = add_list(store, name="Watchlist", limit=2, filters=Filters(min_year=2000))

        result = engine.run(job.id)

        assert result.added == 2
        assert [tmdb for tmdb, _ in radarr["added"]] == [2, 3]

    def test_sort_before_limit(self, engine, store, trakt_items, radarr):
        trakt_items["items"] = [
            movie(1, "Meh", votes=10),
            movie(2, "Great", votes=900),
            movie(3, "Good", votes=500),
        ]
        job = add_list(store, name="Watchlist", limit=2, sort="votes")

        engine.run(job.id)

        assert [tmdb for tmdb, _ in radarr["added"]] == [2, 3]


class TestDryRun:
    def test_dry_run_adds_nothing(self, engine, store, trakt_items, radarr):
        trakt_items["items"] = [movie(1, "One"), movie(2, "Two")]
        job = add_list(store, name="Watchlist")

        result = engine.run(job.id, dry_run=True)

        assert result.dry_run is True
        assert result.added == 2
        assert radarr["added"] == []

    def test_list_level_dry_run_flag(self, engine, store, trakt_items, radarr):
        trakt_items["items"] = [movie(1, "One")]
        job = add_list(store, name="Watchlist", dry_run=True)

        engine.run(job.id)

        assert radarr["added"] == []


class TestFailures:
    def test_partial_when_some_adds_fail(self, engine, store, trakt_items, radarr):
        trakt_items["items"] = [movie(1, "Good"), movie(2, "Bad")]
        radarr["fail"] = {2}
        job = add_list(store, name="Watchlist")

        result = engine.run(job.id)

        assert result.status == "partial"
        assert result.added == 1
        assert result.failed == 1

    def test_error_when_everything_fails(self, engine, store, trakt_items, radarr):
        trakt_items["items"] = [movie(1, "Bad")]
        radarr["fail"] = {1}
        job = add_list(store, name="Watchlist")

        assert engine.run(job.id).status == "error"

    def test_unconfigured_target_reports_clearly(self, engine, store, trakt_items):
        store.mutate(lambda c: setattr(c.radarr, "api_key", ""))
        job = add_list(store, name="Watchlist")

        result = engine.run(job.id)

        assert result.status == "error"
        assert "Radarr is not enabled or not configured" in result.message

    def test_missing_root_folder_reports_clearly(self, engine, store, trakt_items):
        store.mutate(lambda c: setattr(c.radarr, "root_folder", ""))
        job = add_list(store, name="Watchlist")

        assert "root folder" in engine.run(job.id).message

    def test_unknown_list_raises(self, engine):
        with pytest.raises(SyncConfigError):
            engine.run("does-not-exist")


class TestHistory:
    def test_run_is_recorded(self, engine, store, database, trakt_items, radarr):
        trakt_items["items"] = [movie(1, "Kept"), movie(2, "Have it")]
        radarr["library"] = {2}
        job = add_list(store, name="Watchlist")

        result = engine.run(job.id)
        runs = database.recent_runs(limit=5)

        assert len(runs) == 1
        assert runs[0]["status"] == "success"
        assert runs[0]["added"] == 1
        assert runs[0]["existing"] == 1
        assert runs[0]["list_name"] == "Watchlist"

        items = database.run_items(result.run_id)
        actions = {item["title"]: item["action"] for item in items}
        assert actions["Kept (2016)"] == "added"
        assert actions["Have it (2016)"] == "existing"

    def test_filter_reasons_are_recorded(self, engine, store, database, trakt_items, radarr):
        trakt_items["items"] = [movie(1, "Old", year=1980)]
        job = add_list(store, name="Watchlist", filters=Filters(min_year=2000))

        result = engine.run(job.id)
        items = database.run_items(result.run_id)

        assert items[0]["action"] == "filtered"
        assert "before 2000" in items[0]["reason"]

    def test_last_success_timestamp(self, engine, store, database, trakt_items, radarr):
        job = add_list(store, name="Watchlist")
        assert database.last_success_at(job.id) is None
        engine.run(job.id)
        assert database.last_success_at(job.id) is not None


class TestConcurrency:
    def test_running_set_is_empty_after_a_run(self, engine, store, trakt_items, radarr):
        job = add_list(store, name="Watchlist")
        engine.run(job.id)
        assert engine.running_lists() == set()

    def test_running_set_clears_after_failure(self, engine, store, trakt_items):
        store.mutate(lambda c: setattr(c.radarr, "api_key", ""))
        job = add_list(store, name="Watchlist")
        engine.run(job.id)
        assert not engine.is_running(job.id)


class TestSonarrLanguageProfiles:
    def test_language_profile_sent_when_supported(self, engine, store, trakt_items, sonarr):
        trakt_items["items"] = [tvshow(1, "Show")]
        store.mutate(lambda c: setattr(c.sonarr, "language_profile_id", 2))
        job = add_list(store, name="Shows", media_type="show")

        engine.run(job.id)

        assert sonarr["added"][0][1]["language_profile_id"] == 2

    def test_language_profile_omitted_on_v4(self, engine, store, trakt_items, sonarr):
        trakt_items["items"] = [tvshow(1, "Show")]
        sonarr["languages"] = False
        store.mutate(lambda c: setattr(c.sonarr, "language_profile_id", 2))
        job = add_list(store, name="Shows", media_type="show")

        engine.run(job.id)

        assert sonarr["added"][0][1]["language_profile_id"] is None
