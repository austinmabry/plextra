from plextra.config import hash_password


class TestHealthAndPages:
    def test_health_needs_no_auth(self, client):
        response = client.get("/api/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"

    def test_index_is_served(self, client):
        response = client.get("/")
        assert response.status_code == 200
        assert "Plextra" in response.text

    def test_static_assets_are_served(self, client):
        assert client.get("/static/app.js").status_code == 200
        assert client.get("/static/style.css").status_code == 200
        assert client.get("/favicon.svg").status_code == 200


class TestAuth:
    def test_open_by_default(self, client):
        status = client.get("/api/auth/status").json()
        assert status["auth_required"] is False
        assert status["authenticated"] is True
        assert client.get("/api/config").status_code == 200

    def test_setting_a_password_locks_the_api(self, client):
        assert client.put("/api/auth/password", json={"password": "s3cret"}).status_code == 200

        client.cookies.clear()
        assert client.get("/api/config").status_code == 401
        assert client.get("/api/health").status_code == 200

    def test_login_with_the_right_password(self, client):
        client.put("/api/auth/password", json={"password": "s3cret"})
        client.cookies.clear()

        assert client.post("/api/auth/login", json={"password": "nope"}).status_code == 401
        assert client.post("/api/auth/login", json={"password": "s3cret"}).status_code == 200
        assert client.get("/api/config").status_code == 200

    def test_password_can_be_removed(self, client):
        client.put("/api/auth/password", json={"password": "s3cret"})
        client.put("/api/auth/password", json={"password": ""})
        client.cookies.clear()
        assert client.get("/api/config").status_code == 200

    def test_forged_cookie_rejected(self, client):
        client.put("/api/auth/password", json={"password": "s3cret"})
        client.cookies.clear()
        client.cookies.set("plextra_session", "1700000000.deadbeef")
        assert client.get("/api/config").status_code == 401


class TestConfigEndpoints:
    def test_secrets_never_leave_the_server(self, client):
        client.put("/api/auth/password", json={"password": "s3cret"})
        config = client.get("/api/config").json()
        assert config["auth"] == {"enabled": True}
        assert "password_hash" not in config["auth"]
        assert "secret_key" not in config["auth"]

    def test_trakt_tokens_are_not_exposed(self, client):
        config = client.get("/api/config").json()
        assert config["trakt"]["accounts"] == []

    def test_update_radarr(self, client):
        payload = {
            "enabled": True,
            "url": "http://radarr:7878",
            "api_key": "key",
            "quality_profile_id": 4,
            "root_folder": "/movies",
            "minimum_availability": "released",
            "tags": [1],
            "monitored": True,
            "search_on_add": True,
        }
        assert client.put("/api/config/radarr", json=payload).status_code == 200

        config = client.get("/api/config").json()
        assert config["radarr"]["url"] == "http://radarr:7878"
        assert config["radarr"]["quality_profile_id"] == 4

    def test_update_trakt_app_keeps_accounts(self, client):
        client.put("/api/config/trakt", json={"client_id": "abc", "client_secret": "def"})
        config = client.get("/api/config").json()
        assert config["trakt"]["client_id"] == "abc"

    def test_invalid_enum_rejected(self, client):
        response = client.put("/api/config/radarr", json={"minimum_availability": "whenever"})
        assert response.status_code == 422

    def test_unknown_trakt_source_rejected(self, client):
        response = client.post(
            "/api/lists", json={"name": "Bad", "source": {"type": "nonsense"}}
        )
        assert response.status_code == 422
        assert "Unknown Trakt source" in response.text


class TestLists:
    def payload(self, **overrides):
        base = {
            "name": "My watchlist",
            "media_type": "movie",
            "source": {"type": "watchlist"},
            "limit": 5,
            "schedule": {"type": "interval", "hours": 12},
        }
        base.update(overrides)
        return base

    def test_create_and_read(self, client):
        created = client.post("/api/lists", json=self.payload()).json()
        assert created["name"] == "My watchlist"
        assert created["id"]

        listing = client.get("/api/lists").json()["lists"]
        assert len(listing) == 1
        assert listing[0]["limit"] == 5
        assert listing[0]["running"] is False

    def test_defaults_are_filled_in(self, client):
        created = client.post("/api/lists", json={"name": "Bare"}).json()
        assert created["media_type"] == "movie"
        assert created["filters"]["min_year"] == 0
        assert created["enabled"] is True

    def test_update(self, client):
        created = client.post("/api/lists", json=self.payload()).json()
        updated = client.put(
            f"/api/lists/{created['id']}", json=self.payload(name="Renamed", limit=9)
        ).json()

        assert updated["id"] == created["id"]
        assert updated["name"] == "Renamed"
        assert client.get("/api/lists").json()["lists"][0]["limit"] == 9

    def test_delete(self, client):
        created = client.post("/api/lists", json=self.payload()).json()
        assert client.delete(f"/api/lists/{created['id']}").status_code == 200
        assert client.get("/api/lists").json()["lists"] == []

    def test_unknown_list_is_404(self, client):
        assert client.put("/api/lists/nope", json=self.payload()).status_code == 404
        assert client.delete("/api/lists/nope").status_code == 404
        assert client.post("/api/lists/nope/run").status_code == 404

    def test_scheduled_list_gets_a_next_run(self, client):
        client.post("/api/lists", json=self.payload())
        assert client.get("/api/lists").json()["lists"][0]["next_run"]

    def test_manual_list_has_no_next_run(self, client):
        client.post("/api/lists", json=self.payload(schedule={"type": "manual"}))
        assert client.get("/api/lists").json()["lists"][0]["next_run"] is None

    def test_disabled_list_is_not_scheduled(self, client):
        client.post("/api/lists", json=self.payload(enabled=False))
        assert client.get("/api/lists").json()["lists"][0]["next_run"] is None

    def test_bad_cron_does_not_break_the_scheduler(self, client):
        response = client.post(
            "/api/lists", json=self.payload(schedule={"type": "cron", "cron": "not a cron"})
        )
        assert response.status_code == 200
        assert client.get("/api/lists").json()["lists"][0]["next_run"] is None


class TestStatusAndHistory:
    def test_status_shape(self, client):
        status = client.get("/api/status").json()
        assert status["lists"] == {"total": 0, "enabled": 0}
        assert status["radarr"]["configured"] is False
        assert status["totals"]["runs"] == 0

    def test_runs_start_empty(self, client):
        assert client.get("/api/runs").json()["runs"] == []

    def test_logs_endpoint_advances_a_cursor(self, client):
        payload = client.get("/api/logs").json()
        assert "logs" in payload and "cursor" in payload


class TestTraktGuards:
    def test_device_start_needs_a_client_id(self, client):
        response = client.post("/api/trakt/device/start")
        assert response.status_code == 400
        assert "client ID" in response.json()["detail"]

    def test_list_picker_needs_an_account(self, client):
        response = client.get("/api/trakt/lists")
        assert response.status_code == 400


class TestArrGuards:
    def test_radarr_test_without_config(self, client):
        response = client.post("/api/radarr/test", json={})
        assert response.status_code == 400
        assert "URL" in response.json()["detail"]

    def test_sonarr_test_without_api_key(self, client):
        response = client.post("/api/sonarr/test", json={"url": "http://sonarr:8989"})
        assert response.status_code == 400
        assert "API key" in response.json()["detail"]


class TestSeededPassword:
    def test_env_password_seeds_only_when_unset(self, tmp_path, monkeypatch):
        from plextra import settings
        from plextra.config import ConfigStore, verify_password

        monkeypatch.setattr(settings, "ENV_PASSWORD", "from-env")
        config = ConfigStore(tmp_path / "config.json").load()
        assert verify_password("from-env", config.auth.password_hash)

        # A password chosen in the GUI must survive a restart with the env set.
        chosen = hash_password("chosen-in-gui")
        store = ConfigStore(tmp_path / "config.json")
        store.load()
        store.mutate(lambda c: setattr(c.auth, "password_hash", chosen))

        reloaded = ConfigStore(tmp_path / "config.json").load()
        assert verify_password("chosen-in-gui", reloaded.auth.password_hash)
