"""Upgrading from the Plextra era must not lose credentials or history."""

import importlib
import logging

import pytest


@pytest.fixture
def settings_module(monkeypatch, tmp_path):
    """Reimport settings with a clean environment pointed at tmp_path."""

    def reload(**env):
        for key in list(__import__("os").environ):
            if key.startswith(("SIDECARR_", "PLEXTRA_")):
                monkeypatch.delenv(key, raising=False)
        monkeypatch.setenv("SIDECARR_CONFIG_DIR", str(tmp_path))
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        import sidecarr.settings as module

        return importlib.reload(module)

    yield reload
    # Leave the module matching the test session's environment again.
    import sidecarr.settings as module

    importlib.reload(module)


class TestLegacyEnvironment:
    def test_sidecarr_names_win(self, settings_module):
        settings = settings_module(SIDECARR_PORT="1111", PLEXTRA_PORT="2222")
        assert settings.PORT == 1111

    def test_plextra_names_still_work(self, settings_module):
        settings = settings_module(PLEXTRA_PORT="2222")
        assert settings.PORT == 2222

    def test_the_secret_key_falls_back(self, settings_module):
        """Without this, encrypted credentials stop decrypting on upgrade."""
        settings = settings_module(PLEXTRA_SECRET_KEY="correct horse")
        assert settings.SECRET_KEY == "correct horse"

    def test_booleans_fall_back(self, settings_module):
        assert settings_module(PLEXTRA_COOKIE_SECURE="true").COOKIE_SECURE is True

    def test_an_empty_sidecarr_value_is_not_overridden(self, settings_module):
        """Explicitly clearing a variable should mean cleared, not "use the old one"."""
        settings = settings_module(SIDECARR_PASSWORD="", PLEXTRA_PASSWORD="old")
        assert settings.ENV_PASSWORD is None

    def test_deprecation_is_logged_once(self, settings_module, caplog):
        settings = settings_module(PLEXTRA_PORT="2222")
        with caplog.at_level(logging.WARNING):
            settings.warn_about_legacy_env()
        assert "PLEXTRA_PORT" in caplog.text

    def test_nothing_is_logged_when_nothing_is_legacy(self, settings_module, caplog):
        settings = settings_module(SIDECARR_PORT="1111")
        with caplog.at_level(logging.WARNING):
            settings.warn_about_legacy_env()
        assert caplog.text == ""


class TestDatabaseMigration:
    def test_an_old_database_is_adopted(self, settings_module, tmp_path):
        settings = settings_module()
        settings.LEGACY_DB_FILE.write_text("history")
        settings.migrate_legacy_paths()
        assert settings.DB_FILE.read_text() == "history"
        assert not settings.LEGACY_DB_FILE.exists()

    def test_the_write_ahead_log_comes_along(self, settings_module):
        settings = settings_module()
        settings.LEGACY_DB_FILE.write_text("history")
        settings.LEGACY_DB_FILE.with_name("plextra.db-wal").write_text("wal")
        settings.LEGACY_DB_FILE.with_name("plextra.db-shm").write_text("shm")
        settings.migrate_legacy_paths()
        assert settings.DB_FILE.with_name("sidecarr.db-wal").read_text() == "wal"
        assert settings.DB_FILE.with_name("sidecarr.db-shm").read_text() == "shm"

    def test_an_existing_database_is_never_clobbered(self, settings_module):
        settings = settings_module()
        settings.DB_FILE.write_text("current")
        settings.LEGACY_DB_FILE.write_text("old")
        settings.migrate_legacy_paths()
        assert settings.DB_FILE.read_text() == "current"

    def test_a_fresh_install_does_nothing(self, settings_module):
        settings = settings_module()
        settings.migrate_legacy_paths()
        assert not settings.DB_FILE.exists()
