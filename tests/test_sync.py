import pytest

from plextra.clients import ArrError
from plextra.config import Filters, ListJob, Source
from plextra.providers.base import MediaItem, ProviderError
from plextra.sync import SyncConfigError, SyncEngine


def movie(tmdb, title, **overrides):
    base = dict(
        ids={"tmdb": tmdb},
        title=title,
        year=2016,
        runtime=120,
        country="us",
        language="en",
        genres=["drama"],
        rating=7.5,
        votes=1000,
    )
    base.update(overrides)
    return MediaItem(**base)


def tvshow(tvdb, title, **overrides):
    base = dict(
        ids={"tvdb": tvdb},
        title=title,
        year=2022,
        runtime=50,
        country="us",
        language="en",
        network="HBO",
        genres=["drama"],
        rating=8.0,
        votes=500,
    )
    base.update(overrides)
    return MediaItem(**base)


@pytest.fixture
def source_items(monkeypatch):
    """Stand in for whichever provider a list points at."""
    holder = {"items": [], "configured": True, "error": None, "resolved": {}, "name": "TestSource"}

    class FakeProvider:
        name = holder["name"]
        setup_hint = "Set it up."

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self.close()

        def configured(self):
            return holder["configured"]

        def fetch(self, source, media_type, max_items=0):
            if holder["error"]:
                raise ProviderError(holder["error"])
            return list(holder["items"])

        def resolve_ids(self, item, media_type):
            key = "tmdb" if media_type == "movie" else "tvdb"
            extra = holder["resolved"].get(item.imdb_id)
            if extra:
                item.ids[key] = extra

        def close(self):
            holder["closed"] = True

    monkeypatch.setattr(
        "plextra.sync.provider_registry.build", lambda key, config, **kw: FakeProvider()
    )
    return holder


@pytest.fixture
def radarr(monkeypatch):
    state = {
        "library": set(),
        "library_imdb": set(),
        "exclusions": set(),
        "added": [],
        "fail": set(),
        "imdb": {},
        # TMDb IDs Radarr's metadata server cannot serve.
        "unresolvable": set(),
        "bulk_calls": [],
        "single_adds": [],
        "bulk_broken": False,
    }

    class FakeRadarr:
        def __init__(self, url, api_key):
            state["url"] = url

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def system_status(self):
            return {"version": "5.2.6", "appName": "Radarr"}

        def library_ids(self):
            return set(state["library"]), set(state["library_imdb"])

        def exclusion_tmdb_ids(self):
            return set(state["exclusions"])

        def resolve_tmdb_id(self, imdb_id):
            return state["imdb"].get(imdb_id)

        def resolve_for_add(self, tmdb_id, imdb_id=""):
            if tmdb_id in state["unresolvable"]:
                return None
            return {"title": "x", "titleSlug": "x", "tmdbId": tmdb_id}

        @staticmethod
        def unknown_id(tmdb_id, imdb_id=""):
            return ArrError(f"Radarr does not recognise TMDb {tmdb_id}")

        @staticmethod
        def movie_payload(record, **kwargs):
            return {"tmdbId": record["tmdbId"], **kwargs}

        def bulk_add_movies(self, payloads):
            state["bulk_calls"].append(len(payloads))
            if state["bulk_broken"]:
                raise ArrError("bulk endpoint unavailable")
            accepted = []
            for payload in payloads:
                tmdb_id = payload["tmdbId"]
                if tmdb_id in state["fail"]:
                    continue  # omitted from the reply, so the engine retries it alone
                state["added"].append((tmdb_id, payload))
                accepted.append({"tmdbId": tmdb_id})
            return accepted

        def add_movie(self, tmdb_id, **kwargs):
            state["single_adds"].append(tmdb_id)
            if tmdb_id in state["fail"]:
                raise ArrError("Radarr said no")
            if tmdb_id in state["unresolvable"]:
                raise self.unknown_id(tmdb_id)
            kwargs.pop("imdb_id", None)
            state["added"].append((tmdb_id, kwargs))
            return {"id": 1}

    monkeypatch.setattr("plextra.sync.RadarrClient", FakeRadarr)
    return state


@pytest.fixture
def sonarr(monkeypatch):
    state = {
        "library": set(),
        "library_imdb": set(),
        "exclusions": set(),
        "added": [],
        "languages": True,
        "resolve": {},
        "unresolvable": set(),
        "fail": set(),
        "bulk_calls": [],
        "single_adds": [],
    }

    class FakeSonarr:
        def __init__(self, url, api_key):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def system_status(self):
            return {"version": "4.0.0", "appName": "Sonarr"}

        def library_ids(self):
            return set(state["library"]), set(state["library_imdb"])

        def lookup_tvdb(self, tvdb_id):
            if tvdb_id in state["unresolvable"]:
                return None
            return {"title": "x", "titleSlug": "x", "tvdbId": tvdb_id}

        @staticmethod
        def unknown_id(tvdb_id, imdb_id=""):
            return ArrError(f"Sonarr does not recognise TVDb {tvdb_id}")

        def exclusion_tvdb_ids(self):
            return set(state["exclusions"])

        def supports_language_profiles(self):
            return state["languages"]

        def resolve_tvdb_id(self, imdb_id="", tmdb_id=None):
            return state["resolve"].get(imdb_id) or state["resolve"].get(tmdb_id)

        @staticmethod
        def series_payload(record, **kwargs):
            return {"tvdbId": record["tvdbId"], **kwargs}

        def bulk_add_series(self, payloads):
            state["bulk_calls"].append(len(payloads))
            accepted = []
            for payload in payloads:
                tvdb_id = payload["tvdbId"]
                if tvdb_id in state["fail"]:
                    continue
                state["added"].append((tvdb_id, payload))
                accepted.append({"tvdbId": tvdb_id})
            return accepted

        def add_series(self, tvdb_id, **kwargs):
            state["single_adds"].append(tvdb_id)
            # Mirror the real client, which resolves before it posts.
            if tvdb_id in state["unresolvable"]:
                raise self.unknown_id(tvdb_id)
            if tvdb_id in state["fail"]:
                raise ArrError("Sonarr said no")
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
    def test_adds_new_movies(self, engine, store, source_items, radarr):
        source_items["items"] = [movie(1, "One"), movie(2, "Two")]
        job = add_list(store, name="Watchlist")

        result = engine.run(job.id)

        assert result.status == "success"
        assert result.added == 2
        assert [tmdb for tmdb, _ in radarr["added"]] == [1, 2]

    def test_passes_target_settings_through(self, engine, store, source_items, radarr):
        source_items["items"] = [movie(1, "One")]
        store.mutate(lambda c: setattr(c.radarr, "tags", [7]))
        store.mutate(lambda c: setattr(c.radarr, "search_on_add", True))
        job = add_list(store, name="Watchlist")

        engine.run(job.id)

        _, kwargs = radarr["added"][0]
        assert kwargs["quality_profile_id"] == 4
        assert kwargs["root_folder"] == "/movies"
        assert kwargs["tags"] == [7]
        assert kwargs["search_on_add"] is True

    def test_per_list_overrides_win(self, engine, store, source_items, radarr):
        source_items["items"] = [movie(1, "One")]
        job = add_list(
            store, name="4K", quality_profile_id=9, root_folder="/movies4k",
            tags=[3], search_on_add=False,
        )

        engine.run(job.id)

        _, kwargs = radarr["added"][0]
        assert kwargs["quality_profile_id"] == 9
        assert kwargs["root_folder"] == "/movies4k"
        assert kwargs["tags"] == [3]
        assert kwargs["search_on_add"] is False

    def test_adds_shows_via_sonarr(self, engine, store, source_items, sonarr):
        source_items["items"] = [tvshow(101, "Show")]
        job = add_list(store, name="Shows", media_type="show")

        result = engine.run(job.id)

        assert result.added == 1
        tvdb, kwargs = sonarr["added"][0]
        assert tvdb == 101
        assert kwargs["monitor"] == "all"

    def test_works_for_any_provider(self, engine, store, source_items, radarr):
        """The engine does not care which site the list came from."""
        source_items["items"] = [movie(1, "One")]
        job = add_list(store, name="MDBList", source=Source(provider="mdblist", type="list"))

        assert engine.run(job.id).added == 1

    def test_provider_is_closed(self, engine, store, source_items, radarr):
        source_items["items"] = [movie(1, "One")]
        engine.run(add_list(store, name="Watchlist").id)
        assert source_items.get("closed") is True


class TestBulkAdd:
    """One request per batch, not per title.

    Radarr and Sonarr resolve a batch against their metadata service in one go,
    which is what keeps a large first sync from tripping rate limits.
    """

    def test_a_whole_list_goes_in_one_request(self, engine, store, source_items, radarr):
        source_items["items"] = [movie(i, f"Film {i}") for i in range(1, 26)]

        result = engine.run(add_list(store, name="Big list").id)

        assert result.added == 25
        assert radarr["bulk_calls"] == [25]
        assert radarr["single_adds"] == []

    def test_batches_are_capped(self, engine, store, source_items, radarr, monkeypatch):
        monkeypatch.setattr("plextra.sync.settings.BULK_BATCH_SIZE", 10)
        source_items["items"] = [movie(i, f"Film {i}") for i in range(1, 26)]

        engine.run(add_list(store, name="Big list").id)

        assert radarr["bulk_calls"] == [10, 10, 5]

    def test_settings_reach_the_payload(self, engine, store, source_items, radarr):
        source_items["items"] = [movie(1, "One")]
        store.mutate(lambda c: setattr(c.radarr, "tags", [7]))

        engine.run(add_list(store, name="Watchlist").id)

        _, payload = radarr["added"][0]
        assert payload["quality_profile_id"] == 4
        assert payload["root_folder"] == "/movies"
        assert payload["tags"] == [7]

    def test_a_title_the_batch_rejects_is_retried_alone(
        self, engine, store, database, source_items, radarr
    ):
        """The bulk reply says which titles it took, not why it refused one."""
        source_items["items"] = [movie(1, "Good"), movie(2, "Bad")]
        radarr["fail"] = {2}

        result = engine.run(add_list(store, name="Watchlist").id)

        assert (result.added, result.failed) == (1, 1)
        assert radarr["single_adds"] == [2]
        reasons = {i["title"]: i["reason"] for i in database.run_items(result.run_id)}
        assert reasons["Bad (2016)"] == "Radarr said no"

    def test_falls_back_when_the_bulk_endpoint_is_unavailable(
        self, engine, store, source_items, radarr
    ):
        """Older or proxied instances may not serve the import endpoint."""
        source_items["items"] = [movie(1, "One"), movie(2, "Two")]
        radarr["bulk_broken"] = True

        result = engine.run(add_list(store, name="Watchlist").id)

        assert result.added == 2
        assert radarr["single_adds"] == [1, 2]

    def test_shows_batch_too(self, engine, store, source_items, sonarr):
        source_items["items"] = [tvshow(i, f"Show {i}") for i in range(1, 6)]

        result = engine.run(add_list(store, name="Shows", media_type="show").id)

        assert result.added == 5
        assert sonarr["bulk_calls"] == [5]

    def test_dry_run_never_touches_the_bulk_endpoint(
        self, engine, store, source_items, radarr
    ):
        source_items["items"] = [movie(1, "One"), movie(2, "Two")]

        result = engine.run(add_list(store, name="Watchlist").id, dry_run=True)

        assert result.added == 2
        assert radarr["bulk_calls"] == []
        assert radarr["added"] == []


class TestIdResolution:
    def test_resolves_missing_id_via_the_provider(self, engine, store, source_items, radarr):
        """TMDb gives shows a TMDb ID; the provider fills in the TVDb one."""
        source_items["items"] = [movie(0, "IMDb only", ids={"imdb": "tt1"})]
        source_items["resolved"] = {"tt1": 555}
        job = add_list(store, name="List")

        result = engine.run(job.id)

        assert result.added == 1
        assert radarr["added"][0][0] == 555

    def test_falls_back_to_the_arr_lookup(self, engine, store, source_items, radarr):
        """An IMDb-only list still works: Radarr already knows the mapping."""
        source_items["items"] = [movie(0, "IMDb only", ids={"imdb": "tt7"})]
        radarr["imdb"] = {"tt7": 777}
        job = add_list(store, name="List")

        result = engine.run(job.id)

        assert result.added == 1
        assert radarr["added"][0][0] == 777

    def test_sonarr_resolves_from_tmdb(self, engine, store, source_items, sonarr):
        source_items["items"] = [tvshow(0, "Show", ids={"tmdb": 42})]
        sonarr["resolve"] = {42: 4242}
        job = add_list(store, name="Shows", media_type="show")

        engine.run(job.id)

        assert sonarr["added"][0][0] == 4242

    def test_unresolvable_item_is_recorded_not_crashed(self, engine, store, database, source_items, radarr):
        source_items["items"] = [movie(0, "Ghost", ids={"imdb": "tt9"})]
        job = add_list(store, name="List")

        result = engine.run(job.id)

        assert result.added == 0
        assert result.unresolved == 1
        items = database.run_items(result.run_id)
        assert items[0]["action"] == "skipped"
        assert "TMDb ID" in items[0]["reason"]


class TestSkipping:
    def test_skips_titles_already_in_the_library(self, engine, store, source_items, radarr):
        source_items["items"] = [movie(1, "Have it"), movie(2, "Want it")]
        radarr["library"] = {1}
        result = engine.run(add_list(store, name="Watchlist").id)

        assert result.existing == 1
        assert [tmdb for tmdb, _ in radarr["added"]] == [2]

    def test_respects_exclusions(self, engine, store, source_items, radarr):
        source_items["items"] = [movie(1, "Excluded"), movie(2, "Fine")]
        radarr["exclusions"] = {1}
        result = engine.run(add_list(store, name="Watchlist").id)

        assert result.excluded == 1
        assert [tmdb for tmdb, _ in radarr["added"]] == [2]

    def test_applies_filters(self, engine, store, source_items, radarr):
        source_items["items"] = [movie(1, "Old", year=1980), movie(2, "New", year=2020)]
        job = add_list(store, name="Watchlist", filters=Filters(min_year=2000))

        result = engine.run(job.id)

        assert result.filtered == 1
        assert [tmdb for tmdb, _ in radarr["added"]] == [2]

    def test_duplicates_are_collapsed(self, engine, store, source_items, radarr):
        source_items["items"] = [movie(1, "One"), movie(1, "One again")]
        result = engine.run(add_list(store, name="Watchlist").id)

        assert result.added == 1

    def test_skips_a_title_held_under_a_different_tmdb_id(
        self, engine, store, database, source_items, radarr
    ):
        """The same film can sit in Radarr under another TMDb ID.

        Matching on TMDb alone let these through, and the add then failed with
        "path is already configured for an existing movie".
        """
        source_items["items"] = [movie(999, "Songs of War", ids={"tmdb": 999, "imdb": "tt11328608"})]
        radarr["library_imdb"] = {"tt11328608"}

        result = engine.run(add_list(store, name="Watchlist").id)

        assert result.existing == 1
        assert result.added == 0
        assert radarr["added"] == []
        item = database.run_items(result.run_id)[0]
        assert item["action"] == "existing"
        assert "different ID" in item["reason"]

    def test_imdb_match_only_applies_when_the_item_has_one(
        self, engine, store, source_items, radarr
    ):
        source_items["items"] = [movie(1, "No IMDb ID")]
        radarr["library_imdb"] = {"tt11328608"}

        assert engine.run(add_list(store, name="Watchlist").id).added == 1


class TestUnresolvableTitles:
    """Titles Radarr's metadata service genuinely does not recognise."""

    def test_failure_is_recorded_with_a_useful_reason(
        self, engine, store, database, source_items, radarr
    ):
        source_items["items"] = [movie(1, "Cancelled film"), movie(2, "Real film")]
        radarr["unresolvable"] = {1}

        result = engine.run(add_list(store, name="Watchlist").id)

        assert result.status == "partial"
        assert (result.added, result.failed) == (1, 1)
        assert [tmdb for tmdb, _ in radarr["added"]] == [2]

        reasons = {i["title"]: i["reason"] for i in database.run_items(result.run_id)}
        assert "does not recognise" in reasons["Cancelled film (2016)"]

    def test_dry_run_predicts_the_failure(
        self, engine, store, database, source_items, radarr
    ):
        """A dry run that promises an add the real run cannot make is worse
        than useless, so it resolves each title too."""
        source_items["items"] = [movie(1, "Cancelled film"), movie(2, "Real film")]
        radarr["unresolvable"] = {1}

        result = engine.run(add_list(store, name="Watchlist").id, dry_run=True)

        assert (result.added, result.failed) == (1, 1)
        assert radarr["added"] == []  # still writes nothing
        actions = {i["title"]: i["action"] for i in database.run_items(result.run_id)}
        assert actions["Real film (2016)"] == "dry_run"
        assert actions["Cancelled film (2016)"] == "failed"

    def test_shows_too(self, engine, store, source_items, sonarr):
        source_items["items"] = [tvshow(1, "Ghost show"), tvshow(2, "Real show")]
        sonarr["unresolvable"] = {1}

        result = engine.run(add_list(store, name="Shows", media_type="show").id)

        assert (result.added, result.failed) == (1, 1)
        assert [tvdb for tvdb, _ in sonarr["added"]] == [2]


class TestLimitAndSort:
    def test_limit_applies_after_filtering(self, engine, store, source_items, radarr):
        source_items["items"] = [
            movie(1, "Old", year=1980),
            movie(2, "New A", year=2020),
            movie(3, "New B", year=2021),
            movie(4, "New C", year=2022),
        ]
        job = add_list(store, name="Watchlist", limit=2, filters=Filters(min_year=2000))

        result = engine.run(job.id)

        assert result.added == 2
        assert [tmdb for tmdb, _ in radarr["added"]] == [2, 3]

    def test_sort_before_limit(self, engine, store, source_items, radarr):
        source_items["items"] = [
            movie(1, "Meh", votes=10), movie(2, "Great", votes=900), movie(3, "Good", votes=500),
        ]
        engine.run(add_list(store, name="Watchlist", limit=2, sort="votes").id)

        assert [tmdb for tmdb, _ in radarr["added"]] == [2, 3]

    def test_library_hits_do_not_consume_the_limit(self, engine, store, source_items, radarr):
        """Ask for 2 new titles and get 2, not 2 minus what you already own."""
        source_items["items"] = [movie(1, "Have"), movie(2, "A"), movie(3, "B")]
        radarr["library"] = {1}
        engine.run(add_list(store, name="Watchlist", limit=2).id)

        assert [tmdb for tmdb, _ in radarr["added"]] == [2, 3]


class TestDryRun:
    def test_dry_run_adds_nothing(self, engine, store, source_items, radarr):
        source_items["items"] = [movie(1, "One"), movie(2, "Two")]
        result = engine.run(add_list(store, name="Watchlist").id, dry_run=True)

        assert result.dry_run is True
        assert result.added == 2
        assert radarr["added"] == []

    def test_list_level_dry_run_flag(self, engine, store, source_items, radarr):
        source_items["items"] = [movie(1, "One")]
        engine.run(add_list(store, name="Watchlist", dry_run=True).id)
        assert radarr["added"] == []


class TestFailures:
    def test_partial_when_some_adds_fail(self, engine, store, source_items, radarr):
        source_items["items"] = [movie(1, "Good"), movie(2, "Bad")]
        radarr["fail"] = {2}
        result = engine.run(add_list(store, name="Watchlist").id)

        assert result.status == "partial"
        assert (result.added, result.failed) == (1, 1)

    def test_error_when_everything_fails(self, engine, store, source_items, radarr):
        source_items["items"] = [movie(1, "Bad")]
        radarr["fail"] = {1}
        assert engine.run(add_list(store, name="Watchlist").id).status == "error"

    def test_unconfigured_target_reports_clearly(self, engine, store, source_items):
        store.mutate(lambda c: setattr(c.radarr, "api_key", ""))
        result = engine.run(add_list(store, name="Watchlist").id)

        assert result.status == "error"
        assert "Radarr is not enabled or not configured" in result.message

    def test_unconfigured_provider_reports_its_setup_hint(self, engine, store, source_items, radarr):
        source_items["configured"] = False
        result = engine.run(add_list(store, name="Watchlist").id)

        assert result.status == "error"
        assert "not set up yet" in result.message
        assert "Set it up." in result.message

    def test_provider_error_is_surfaced(self, engine, store, source_items, radarr):
        source_items["error"] = "That list is private."
        result = engine.run(add_list(store, name="Watchlist").id)

        assert result.status == "error"
        assert result.message == "That list is private."

    def test_unknown_list_raises(self, engine):
        with pytest.raises(SyncConfigError):
            engine.run("does-not-exist")


class TestPreflightAndPause:
    """A target that is simply down should not fill History with failures."""

    def test_preflight_passes_when_the_target_answers(self, engine, store, radarr):
        job = add_list(store, name="Watchlist")
        assert engine.preflight(job.id) is None

    def test_preflight_reports_an_unconfigured_target(self, engine, store, radarr):
        store.mutate(lambda c: setattr(c.radarr, "api_key", ""))
        job = add_list(store, name="Watchlist")
        assert "not enabled or not configured" in engine.preflight(job.id)

    def test_preflight_reports_an_unreachable_target(self, engine, store, monkeypatch):
        from plextra.clients import ArrError

        class DeadRadarr:
            def __init__(self, url, api_key):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def system_status(self):
                raise ArrError("Connection refused")

        monkeypatch.setattr("plextra.sync.RadarrClient", DeadRadarr)
        job = add_list(store, name="Watchlist")
        assert "not responding" in engine.preflight(job.id)

    def test_a_scheduled_run_is_skipped_while_paused(
        self, engine, store, database, source_items, radarr
    ):
        from plextra.scheduler import SyncScheduler

        source_items["items"] = [movie(1, "One")]
        job = add_list(store, name="Watchlist")
        store.mutate(lambda c: setattr(c.scheduler, "paused", True))

        SyncScheduler(store, engine, database)._run_job(job.id)

        assert radarr["added"] == []
        assert database.recent_runs(limit=5) == []

    def test_a_manual_run_still_works_while_paused(
        self, engine, store, source_items, radarr
    ):
        """Pausing stops the schedule, not the Run button."""
        source_items["items"] = [movie(1, "One")]
        job = add_list(store, name="Watchlist")
        store.mutate(lambda c: setattr(c.scheduler, "paused", True))

        assert engine.run(job.id).added == 1


class TestHistory:
    def test_run_is_recorded(self, engine, store, database, source_items, radarr):
        source_items["items"] = [movie(1, "Kept"), movie(2, "Have it")]
        radarr["library"] = {2}
        job = add_list(store, name="Watchlist")

        result = engine.run(job.id)
        runs = database.recent_runs(limit=5)

        assert len(runs) == 1
        assert runs[0]["status"] == "success"
        assert (runs[0]["added"], runs[0]["existing"]) == (1, 1)

        actions = {i["title"]: i["action"] for i in database.run_items(result.run_id)}
        assert actions["Kept (2016)"] == "added"
        assert actions["Have it (2016)"] == "existing"

    def test_filter_reasons_name_the_provider(self, engine, store, database, source_items, radarr):
        source_items["items"] = [movie(1, "Old", year=1980)]
        job = add_list(store, name="Watchlist", filters=Filters(min_year=2000))

        items = database.run_items(engine.run(job.id).run_id)

        assert items[0]["action"] == "filtered"
        assert "before 2000" in items[0]["reason"]

    def test_last_success_timestamp(self, engine, store, database, source_items, radarr):
        job = add_list(store, name="Watchlist")
        assert database.last_success_at(job.id) is None
        engine.run(job.id)
        assert database.last_success_at(job.id) is not None


class TestConcurrency:
    def test_running_set_is_empty_after_a_run(self, engine, store, source_items, radarr):
        job = add_list(store, name="Watchlist")
        engine.run(job.id)
        assert engine.running_lists() == set()

    def test_running_set_clears_after_failure(self, engine, store, source_items):
        store.mutate(lambda c: setattr(c.radarr, "api_key", ""))
        job = add_list(store, name="Watchlist")
        engine.run(job.id)
        assert not engine.is_running(job.id)


class TestSonarrLanguageProfiles:
    def test_language_profile_sent_when_supported(self, engine, store, source_items, sonarr):
        source_items["items"] = [tvshow(1, "Show")]
        store.mutate(lambda c: setattr(c.sonarr, "language_profile_id", 2))
        engine.run(add_list(store, name="Shows", media_type="show").id)

        assert sonarr["added"][0][1]["language_profile_id"] == 2

    def test_language_profile_omitted_on_v4(self, engine, store, source_items, sonarr):
        source_items["items"] = [tvshow(1, "Show")]
        sonarr["languages"] = False
        store.mutate(lambda c: setattr(c.sonarr, "language_profile_id", 2))
        engine.run(add_list(store, name="Shows", media_type="show").id)

        assert sonarr["added"][0][1]["language_profile_id"] is None
