from sidecarr.config import hash_password


class TestHealthAndPages:
    def test_health_needs_no_auth(self, client):
        response = client.get("/api/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"

    def test_index_is_served(self, client):
        response = client.get("/")
        assert response.status_code == 200
        assert "Sidecarr" in response.text

    def test_static_assets_are_served(self, client):
        assert client.get("/static/app.js").status_code == 200
        assert client.get("/static/style.css").status_code == 200
        assert client.get("/favicon.svg").status_code == 200


class TestCsrf:
    """Everything here mutates a real library, so a cross-site page must not
    be able to drive it just by knowing the port."""

    def test_a_mutating_request_without_a_token_is_refused(self, raw_client):
        response = raw_client.post("/api/lists", json={"name": "Injected"})
        assert response.status_code == 403
        assert "CSRF" in response.json()["detail"]

    def test_the_refusal_explains_what_to_do(self, raw_client):
        detail = raw_client.post("/api/lists", json={}).json()["detail"]
        assert "sidecarr_csrf" in detail and "X-CSRF-Token" in detail

    def test_a_wrong_token_is_refused(self, raw_client):
        raw_client.get("/api/health")
        raw_client.headers["X-CSRF-Token"] = "not-the-real-token"
        assert raw_client.post("/api/lists", json={"name": "X"}).status_code == 403

    def test_reads_are_unaffected(self, raw_client):
        assert raw_client.get("/api/health").status_code == 200
        assert raw_client.get("/api/config").status_code == 200

    def test_a_get_hands_out_a_token(self, raw_client):
        raw_client.get("/api/health")
        assert raw_client.cookies.get("sidecarr_csrf")

    def test_echoing_the_cookie_back_works(self, raw_client):
        raw_client.get("/api/health")
        raw_client.headers["X-CSRF-Token"] = raw_client.cookies.get("sidecarr_csrf")
        assert raw_client.post("/api/lists", json={"name": "Fine"}).status_code == 200

    def test_nothing_was_created_by_the_refused_request(self, raw_client):
        raw_client.post("/api/lists", json={"name": "Injected"})
        assert raw_client.get("/api/lists").json()["lists"] == []

    def test_the_refusal_mentions_the_upgrade_case(self, raw_client):
        detail = raw_client.post("/api/lists", json={}).json()["detail"]
        assert "cache" in detail

    def test_a_cross_site_request_is_still_refused_with_the_cookie(self, raw_client):
        """The cookie rides along on a cross-site POST; only the header cannot."""
        raw_client.get("/api/health")
        response = raw_client.post(
            "/api/lists", json={"name": "Injected"}, headers={"Sec-Fetch-Site": "cross-site"}
        )
        assert response.status_code == 403
        assert raw_client.get("/api/lists").json()["lists"] == []

    def test_same_site_is_not_enough(self, raw_client):
        """A sibling subdomain is not this origin, so it gets no free pass."""
        raw_client.get("/api/health")
        response = raw_client.post(
            "/api/lists", json={"name": "Injected"}, headers={"Sec-Fetch-Site": "same-site"}
        )
        assert response.status_code == 403


class TestStaleClientAfterUpgrade:
    """0.3.0 added CSRF, so a browser still running a cached 0.2.x app.js sent no
    token at all and every Run, Save and Delete came back 403. The browser's own
    Sec-Fetch-Site is proof enough that such a request is not cross-site."""

    def test_a_cached_pre_csrf_client_still_works(self, raw_client):
        response = raw_client.post(
            "/api/lists",
            json={"name": "From an old page"},
            headers={"Sec-Fetch-Site": "same-origin"},
        )
        assert response.status_code == 200

    def test_it_works_without_the_cookie_at_all(self, raw_client):
        response = raw_client.post(
            "/api/lists", json={"name": "No cookie"}, headers={"Sec-Fetch-Site": "same-origin"}
        )
        assert response.status_code == 200

    def test_a_direct_navigation_counts_as_same_origin(self, raw_client):
        response = raw_client.post(
            "/api/lists", json={"name": "Typed in"}, headers={"Sec-Fetch-Site": "none"}
        )
        assert response.status_code == 200


class TestAssetCaching:
    """The stale-cache trap that broke 0.3.0 must not be reachable again."""

    def test_asset_urls_carry_a_version(self, client):
        body = client.get("/").text
        assert "/static/app.js?v=" in body
        assert "/static/style.css?v=" in body

    def test_the_page_itself_is_never_cached(self, client):
        assert client.get("/").headers["Cache-Control"] == "no-store"

    def test_assets_must_be_revalidated(self, client):
        assert client.get("/static/app.js").headers["Cache-Control"] == "no-cache"

    def test_the_version_changes_when_an_asset_changes(self, client, monkeypatch, tmp_path):
        from sidecarr import api as api_module

        before = api_module.asset_version()

        static = tmp_path / "static"
        static.mkdir()
        (static / "app.js").write_text("// different")
        (static / "style.css").write_text("/* different */")
        monkeypatch.setattr(api_module.settings, "WEB_DIR", tmp_path)

        assert api_module.asset_version() != before

    def test_the_version_is_stable_across_calls(self, client):
        from sidecarr import api as api_module

        assert api_module.asset_version() == api_module.asset_version()


class TestSecurityHeaders:
    def test_headers_are_set(self, client):
        headers = client.get("/").headers
        assert headers["X-Frame-Options"] == "DENY"
        assert headers["X-Content-Type-Options"] == "nosniff"
        assert headers["Referrer-Policy"] == "no-referrer"

    def test_the_csp_blocks_framing_and_foreign_script(self, client):
        csp = client.get("/").headers["Content-Security-Policy"]
        assert "frame-ancestors 'none'" in csp
        assert "script-src 'self'" in csp
        assert "default-src 'self'" in csp

    def test_the_csp_allows_no_inline_script(self, client):
        """All script lives in static files, so this can stay strict."""
        csp = client.get("/").headers["Content-Security-Policy"]
        script = next(p for p in csp.split("; ") if p.startswith("script-src"))
        assert "unsafe-inline" not in script and "unsafe-eval" not in script


class TestAuth:
    def test_open_by_default(self, client):
        status = client.get("/api/auth/status").json()
        assert status["auth_required"] is False
        assert status["authenticated"] is True
        assert client.get("/api/config").status_code == 200

    def test_setting_a_password_locks_the_api(self, client):
        assert client.put("/api/auth/password", json={"password": "s3cret"}).status_code == 200

        client.cookies.delete("sidecarr_session")
        assert client.get("/api/config").status_code == 401
        assert client.get("/api/health").status_code == 200

    def test_login_with_the_right_password(self, client):
        client.put("/api/auth/password", json={"password": "s3cret"})
        client.cookies.delete("sidecarr_session")

        assert client.post("/api/auth/login", json={"password": "nope"}).status_code == 401
        assert client.post("/api/auth/login", json={"password": "s3cret"}).status_code == 200
        assert client.get("/api/config").status_code == 200

    def test_password_can_be_removed(self, client):
        client.put("/api/auth/password", json={"password": "s3cret"})
        client.put("/api/auth/password", json={"password": ""})
        client.cookies.delete("sidecarr_session")
        assert client.get("/api/config").status_code == 200

    def test_forged_cookie_rejected(self, client):
        client.put("/api/auth/password", json={"password": "s3cret"})
        client.cookies.delete("sidecarr_session")
        client.cookies.set("sidecarr_session", "1700000000.deadbeef")
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

    def test_unknown_source_rejected(self, client):
        response = client.post(
            "/api/lists", json={"name": "Bad", "source": {"type": "nonsense"}}
        )
        assert response.status_code == 422
        assert "Trakt has no source 'nonsense'" in response.text

    def test_unknown_provider_rejected(self, client):
        response = client.post(
            "/api/lists", json={"name": "Bad", "source": {"provider": "letterboxd"}}
        )
        assert response.status_code == 422
        assert "Unknown list source" in response.text

    def test_media_type_mismatch_rejected(self, client):
        """Box office is movies only; catching it here beats failing at 3am."""
        response = client.post(
            "/api/lists",
            json={"name": "Bad", "media_type": "show", "source": {"type": "boxoffice"}},
        )
        assert response.status_code == 422
        assert "does not support shows" in response.text

    def test_provider_credentials_round_trip(self, client):
        assert client.put("/api/config/tmdb", json={"api_key": "tk"}).status_code == 200
        assert client.put("/api/config/mdblist", json={"api_key": "mk"}).status_code == 200
        assert client.put("/api/config/plex", json={"token": "pt"}).status_code == 200

        config = client.get("/api/config").json()
        assert config["tmdb"]["api_key"] == "tk"
        assert config["mdblist"]["api_key"] == "mk"
        assert config["plex"]["token"] == "pt"


class TestProviders:
    def test_every_provider_is_described(self, client):
        payload = client.get("/api/providers").json()["providers"]
        keys = {p["key"] for p in payload}
        assert keys == {
            "trakt", "tmdb", "mdblist", "imdb", "plex", "stevenlu", "arr", "text", "custom",
        }

    def test_descriptor_carries_what_the_editor_needs(self, client):
        payload = client.get("/api/providers").json()["providers"]
        tmdb = next(p for p in payload if p["key"] == "tmdb")

        assert tmdb["configured"] is False  # no API key yet
        assert tmdb["setup_hint"]
        collection = next(s for s in tmdb["sources"] if s["key"] == "collection")
        assert collection["media"] == ["movie"]
        assert collection["fields"][0]["key"] == "collection_id"

    def test_keyless_providers_are_configured_out_of_the_box(self, client):
        payload = {p["key"]: p for p in client.get("/api/providers").json()["providers"]}
        for key in ("imdb", "stevenlu", "custom", "arr"):
            assert payload[key]["configured"] is True, key

    def test_status_reports_provider_readiness(self, client):
        status = client.get("/api/status").json()
        by_key = {p["key"]: p for p in status["providers"]}
        assert by_key["trakt"]["configured"] is False
        assert by_key["custom"]["configured"] is True

        client.put("/api/config/tmdb", json={"api_key": "tk"})
        status = client.get("/api/status").json()
        assert {p["key"]: p for p in status["providers"]}["tmdb"]["configured"] is True

    def test_unknown_provider_test_is_404(self, client):
        assert client.post("/api/providers/nope/test").status_code == 404

    def test_testing_an_unconfigured_provider_explains_itself(self, client):
        response = client.post("/api/providers/tmdb/test")
        assert response.status_code == 400
        assert "TMDb API key" in response.json()["detail"]

    def test_list_picker_rejects_providers_without_one(self, client):
        response = client.get("/api/providers/imdb/lists")
        assert response.status_code == 400
        assert "cannot list your lists" in response.json()["detail"]


class TestListsAcrossProviders:
    def test_a_list_can_use_any_provider(self, client):
        created = client.post(
            "/api/lists",
            json={
                "name": "My MDBList",
                "source": {"provider": "mdblist", "type": "list", "list_url": "someone/faves"},
            },
        ).json()
        assert created["source"]["provider"] == "mdblist"

    def test_provider_specific_options_are_stored(self, client):
        created = client.post(
            "/api/lists",
            json={
                "name": "Marvel",
                "source": {
                    "provider": "tmdb",
                    "type": "company",
                    "options": {"company_id": "420"},
                },
            },
        ).json()
        assert created["source"]["options"]["company_id"] == "420"

    def test_custom_url_list(self, client):
        created = client.post(
            "/api/lists",
            json={
                "name": "My feed",
                "media_type": "show",
                "source": {
                    "provider": "custom",
                    "type": "url",
                    "options": {"url": "https://example.com/list.json"},
                },
            },
        ).json()
        assert created["source"]["options"]["url"] == "https://example.com/list.json"

    def test_existing_trakt_lists_still_default_to_trakt(self, client):
        """A config written before providers existed must keep working."""
        created = client.post(
            "/api/lists", json={"name": "Old", "source": {"type": "watchlist"}}
        ).json()
        assert created["source"]["provider"] == "trakt"


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
        from sidecarr import settings
        from sidecarr.config import ConfigStore, verify_password

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
