"""Credentials are encrypted at rest, and old plaintext configs still load."""

import json
import os

import pytest

from plextra.config import ConfigStore
from plextra.crypto import PREFIX, SecretBox, transform_secrets


class TestSecretBox:
    def test_round_trip(self, tmp_path):
        box = SecretBox(tmp_path)
        assert box.decrypt(box.encrypt("hunter2")) == "hunter2"

    def test_ciphertext_does_not_contain_the_secret(self, tmp_path):
        token = SecretBox(tmp_path).encrypt("super-secret-key")
        assert "super-secret-key" not in token
        assert token.startswith(PREFIX)

    def test_plaintext_passes_through_undecrypted(self, tmp_path):
        """Configs written before encryption existed must still load."""
        assert SecretBox(tmp_path).decrypt("plain-api-key") == "plain-api-key"

    def test_encrypting_twice_is_a_no_op(self, tmp_path):
        box = SecretBox(tmp_path)
        once = box.encrypt("value")
        assert box.encrypt(once) == once

    def test_empty_values_are_left_alone(self, tmp_path):
        box = SecretBox(tmp_path)
        assert box.encrypt("") == ""
        assert box.decrypt("") == ""

    def test_the_key_file_is_owner_only(self, tmp_path):
        SecretBox(tmp_path).encrypt("x")
        assert (tmp_path / "secret.key").stat().st_mode & 0o777 == 0o600

    def test_the_same_key_file_is_reused(self, tmp_path):
        token = SecretBox(tmp_path).encrypt("value")
        assert SecretBox(tmp_path).decrypt(token) == "value"

    def test_a_passphrase_keeps_the_key_off_disk(self, tmp_path):
        box = SecretBox(tmp_path, passphrase="correct horse battery staple")
        token = box.encrypt("value")
        assert not (tmp_path / "secret.key").exists()
        assert SecretBox(tmp_path, passphrase="correct horse battery staple").decrypt(token) == "value"

    def test_the_wrong_passphrase_does_not_return_the_secret(self, tmp_path):
        token = SecretBox(tmp_path, passphrase="right").encrypt("value")
        assert SecretBox(tmp_path, passphrase="wrong").decrypt(token) == ""

    def test_a_lost_key_fails_closed_rather_than_crashing(self, tmp_path):
        token = SecretBox(tmp_path).encrypt("value")
        (tmp_path / "secret.key").unlink()
        assert SecretBox(tmp_path).decrypt(token) == ""


class TestSecretPaths:
    def sample(self):
        return {
            "trakt": {
                "client_id": "public-id",
                "client_secret": "secret",
                "accounts": {"someone": {"access_token": "at", "refresh_token": "rt"}},
            },
            "tmdb": {"api_key": "tk"},
            "mdblist": {"api_key": "mk"},
            "plex": {"token": "pt"},
            "radarr": {"url": "http://radarr:7878", "api_key": "rk"},
            "sonarr": {"api_key": "sk"},
        }

    def test_every_credential_is_covered(self):
        data = transform_secrets(self.sample(), lambda v: "TOUCHED")
        assert data["trakt"]["client_secret"] == "TOUCHED"
        assert data["trakt"]["accounts"]["someone"]["access_token"] == "TOUCHED"
        assert data["trakt"]["accounts"]["someone"]["refresh_token"] == "TOUCHED"
        assert data["tmdb"]["api_key"] == "TOUCHED"
        assert data["mdblist"]["api_key"] == "TOUCHED"
        assert data["plex"]["token"] == "TOUCHED"
        assert data["radarr"]["api_key"] == "TOUCHED"
        assert data["sonarr"]["api_key"] == "TOUCHED"

    def test_non_credentials_are_left_alone(self):
        data = transform_secrets(self.sample(), lambda v: "TOUCHED")
        assert data["trakt"]["client_id"] == "public-id"
        assert data["radarr"]["url"] == "http://radarr:7878"

    def test_missing_sections_do_not_raise(self):
        assert transform_secrets({}, lambda v: "TOUCHED") == {}
        transform_secrets({"trakt": {}}, lambda v: "TOUCHED")


class TestConfigStoreIntegration:
    def test_credentials_are_not_readable_on_disk(self, tmp_path):
        store = ConfigStore(tmp_path / "config.json")
        store.load()
        store.mutate(lambda c: setattr(c.radarr, "api_key", "my-radarr-key"))
        store.mutate(lambda c: setattr(c.tmdb, "api_key", "my-tmdb-key"))

        raw = (tmp_path / "config.json").read_text()
        assert "my-radarr-key" not in raw
        assert "my-tmdb-key" not in raw
        assert PREFIX in raw

    def test_they_come_back_intact(self, tmp_path):
        path = tmp_path / "config.json"
        store = ConfigStore(path)
        store.load()
        store.mutate(lambda c: setattr(c.radarr, "api_key", "my-radarr-key"))

        assert ConfigStore(path).load().radarr.api_key == "my-radarr-key"

    def test_a_plaintext_config_is_migrated_on_first_save(self, tmp_path):
        """Upgrading must not lock anyone out of their own settings."""
        path = tmp_path / "config.json"
        path.write_text(json.dumps({
            "radarr": {"api_key": "legacy-key", "url": "http://radarr:7878"},
            "trakt": {"client_secret": "legacy-secret"},
        }))

        config = ConfigStore(path).load()
        assert config.radarr.api_key == "legacy-key"
        assert config.trakt.client_secret == "legacy-secret"

        # load() saves, which rewrites the file encrypted.
        raw = path.read_text()
        assert "legacy-key" not in raw
        assert "legacy-secret" not in raw

    def test_urls_stay_readable_for_troubleshooting(self, tmp_path):
        path = tmp_path / "config.json"
        store = ConfigStore(path)
        store.load()
        store.mutate(lambda c: setattr(c.radarr, "url", "http://radarr:7878"))
        assert "http://radarr:7878" in path.read_text()

    def test_trakt_tokens_are_encrypted(self, tmp_path):
        from plextra.config import TraktAccount

        path = tmp_path / "config.json"
        store = ConfigStore(path)
        store.load()
        store.mutate(
            lambda c: c.trakt.accounts.__setitem__(
                "someone", TraktAccount(access_token="at-secret", refresh_token="rt-secret")
            )
        )

        raw = path.read_text()
        assert "at-secret" not in raw
        assert "rt-secret" not in raw
        assert ConfigStore(path).load().trakt.accounts["someone"].access_token == "at-secret"
