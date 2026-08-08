import json

from sidecarr.config import (
    AppConfig,
    ConfigStore,
    ListJob,
    hash_password,
    verify_password,
)


class TestPasswords:
    def test_round_trip(self):
        stored = hash_password("hunter2")
        assert verify_password("hunter2", stored)
        assert not verify_password("hunter3", stored)

    def test_salted(self):
        assert hash_password("same") != hash_password("same")

    def test_garbage_never_verifies(self):
        assert not verify_password("x", "")
        assert not verify_password("x", "not-a-hash")
        assert not verify_password("x", "md5$aa$bb")


class TestStore:
    def test_creates_file_on_first_load(self, tmp_path):
        store = ConfigStore(tmp_path / "config.json")
        config = store.load()
        assert (tmp_path / "config.json").exists()
        assert config.lists == []
        assert config.auth.secret_key

    def test_persists_changes(self, tmp_path):
        path = tmp_path / "config.json"
        store = ConfigStore(path)
        store.load()
        store.mutate(lambda c: c.lists.append(ListJob(name="Watchlist")))

        reloaded = ConfigStore(path).load()
        assert [job.name for job in reloaded.lists] == ["Watchlist"]

    def test_secret_key_is_stable_across_loads(self, tmp_path):
        path = tmp_path / "config.json"
        first = ConfigStore(path).load().auth.secret_key
        second = ConfigStore(path).load().auth.secret_key
        assert first == second

    def test_missing_fields_get_defaults(self, tmp_path):
        """A config written by an older version must still load."""
        path = tmp_path / "config.json"
        path.write_text(json.dumps({"radarr": {"url": "http://radarr:7878"}}))

        config = ConfigStore(path).load()
        assert config.radarr.url == "http://radarr:7878"
        assert config.radarr.minimum_availability == "released"
        assert config.sonarr.season_folder is True
        assert config.lists == []

    def test_unknown_fields_are_dropped(self, tmp_path):
        path = tmp_path / "config.json"
        path.write_text(json.dumps({"radarr": {"url": "http://x", "legacy_option": 1}}))
        config = ConfigStore(path).load()
        assert not hasattr(config.radarr, "legacy_option")

    def test_file_is_owner_readable_only(self, tmp_path):
        path = tmp_path / "config.json"
        ConfigStore(path).load()
        assert (path.stat().st_mode & 0o777) == 0o600

    def test_atomic_write_leaves_no_temp_file(self, tmp_path):
        store = ConfigStore(tmp_path / "config.json")
        store.load()
        store.save()
        assert list(tmp_path.glob("*.tmp")) == []


class TestModels:
    def test_list_defaults_are_permissive(self):
        job = ListJob()
        assert job.limit == 0
        assert job.filters.min_year == 0
        assert job.filters.max_year == 0
        assert job.search_on_add is None

    def test_each_list_gets_its_own_id(self):
        assert ListJob().id != ListJob().id

    def test_filters_are_not_shared_between_lists(self):
        first, second = ListJob(), ListJob()
        first.filters.blacklisted_genres.append("horror")
        assert second.filters.blacklisted_genres == []

    def test_configured_properties(self):
        config = AppConfig()
        assert not config.radarr.configured
        config.radarr.enabled = True
        config.radarr.url = "http://radarr:7878"
        assert not config.radarr.configured  # still no API key
        config.radarr.api_key = "key"
        assert config.radarr.configured

    def test_find_list(self):
        job = ListJob(name="A")
        config = AppConfig(lists=[job])
        assert config.find_list(job.id) is job
        assert config.find_list("nope") is None
