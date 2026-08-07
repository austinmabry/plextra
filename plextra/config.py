"""Configuration models and the on-disk config store.

Config lives in a single JSON file under the mounted /config volume. Models are
pydantic so that a config written by an older version is upgraded on load: any
field the file is missing simply falls back to its default.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import secrets
import threading
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator

from . import settings

log = logging.getLogger(__name__)

CONFIG_VERSION = 1

MediaType = Literal["movie", "show"]

SOURCE_TYPES = (
    "watchlist",
    "list",
    "collection",
    "recommended",
    "trending",
    "popular",
    "anticipated",
    "boxoffice",
    "watched",
    "played",
    "person",
)


# --------------------------------------------------------------------------- #
# Password hashing
# --------------------------------------------------------------------------- #

_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(
        password.encode(), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=32
    )
    return f"scrypt${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, salt_hex, dk_hex = stored.split("$")
        if scheme != "scrypt":
            return False
        dk = hashlib.scrypt(
            password.encode(),
            salt=bytes.fromhex(salt_hex),
            n=_SCRYPT_N,
            r=_SCRYPT_R,
            p=_SCRYPT_P,
            dklen=32,
        )
    except Exception:
        return False
    return hmac.compare_digest(dk.hex(), dk_hex)


# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #


class TraktAccount(BaseModel):
    access_token: str = ""
    refresh_token: str = ""
    created_at: int = 0
    expires_in: int = 0
    scope: str = "public"
    token_type: str = "Bearer"


class TraktConfig(BaseModel):
    client_id: str = ""
    client_secret: str = ""
    accounts: dict[str, TraktAccount] = Field(default_factory=dict)

    @property
    def configured(self) -> bool:
        return bool(self.client_id and self.client_secret)

    def default_account(self) -> str:
        return next(iter(self.accounts), "")


class RadarrConfig(BaseModel):
    enabled: bool = False
    url: str = ""
    api_key: str = ""
    quality_profile_id: int | None = None
    root_folder: str = ""
    minimum_availability: Literal["announced", "inCinemas", "released"] = "released"
    tags: list[int] = Field(default_factory=list)
    monitored: bool = True
    search_on_add: bool = True

    @property
    def configured(self) -> bool:
        return bool(self.enabled and self.url and self.api_key)


class SonarrConfig(BaseModel):
    enabled: bool = False
    url: str = ""
    api_key: str = ""
    quality_profile_id: int | None = None
    # Sonarr v3 only; ignored (and not sent) when the instance is v4+.
    language_profile_id: int | None = None
    root_folder: str = ""
    season_folder: bool = True
    series_type: Literal["standard", "daily", "anime"] = "standard"
    monitor: Literal[
        "all", "future", "missing", "existing", "firstSeason", "lastSeason", "pilot", "none"
    ] = "all"
    tags: list[int] = Field(default_factory=list)
    monitored: bool = True
    search_on_add: bool = False

    @property
    def configured(self) -> bool:
        return bool(self.enabled and self.url and self.api_key)


class Filters(BaseModel):
    """traktarr-compatible filters. Every numeric filter is disabled at 0.

    For ``allowed_countries`` / ``allowed_languages``:
      * ``[]``          -> allow anything that has a value
      * ``["ignore"]``  -> allow anything, including items missing the field
      * ``["us","gb"]`` -> allow only those values
    """

    allowed_countries: list[str] = Field(default_factory=list)
    allowed_languages: list[str] = Field(default_factory=list)
    blacklisted_genres: list[str] = Field(default_factory=list)
    blacklisted_networks: list[str] = Field(default_factory=list)
    blacklisted_title_keywords: list[str] = Field(default_factory=list)
    blacklisted_ids: list[int] = Field(default_factory=list)
    min_year: int = 0
    max_year: int = 0
    min_runtime: int = 0
    max_runtime: int = 0
    min_rating: float = 0.0
    min_votes: int = 0


class Source(BaseModel):
    type: str = "watchlist"
    # Trakt username whose OAuth token authenticates the request.
    account: str = ""
    # For type == "list": full Trakt list URL, or "user/list-slug".
    list_url: str = ""
    # For type == "person": e.g. "Denis Villeneuve".
    person: str = ""
    # For type in ("watched", "played").
    period: Literal["daily", "weekly", "monthly", "yearly", "all"] = "weekly"

    @field_validator("type")
    @classmethod
    def _known_source(cls, value: str) -> str:
        if value not in SOURCE_TYPES:
            raise ValueError(
                f"Unknown Trakt source {value!r}. Pick one of: {', '.join(SOURCE_TYPES)}."
            )
        return value


class Schedule(BaseModel):
    type: Literal["manual", "interval", "cron"] = "interval"
    hours: float = 24.0
    cron: str = "0 3 * * *"


class ListJob(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    name: str = "New list"
    enabled: bool = True
    media_type: MediaType = "movie"
    source: Source = Field(default_factory=Source)
    # 0 == no limit. Applied after filtering, so you get N items that passed.
    limit: int = 0
    sort: Literal["none", "votes", "rating", "released"] = "none"
    filters: Filters = Field(default_factory=Filters)
    schedule: Schedule = Field(default_factory=Schedule)
    dry_run: bool = False

    # Per-list overrides; None/"" means "inherit the Radarr/Sonarr default".
    search_on_add: bool | None = None
    quality_profile_id: int | None = None
    root_folder: str = ""
    tags: list[int] = Field(default_factory=list)


class AuthConfig(BaseModel):
    password_hash: str = ""
    secret_key: str = Field(default_factory=lambda: secrets.token_hex(32))

    @property
    def enabled(self) -> bool:
        return bool(self.password_hash)


class AppConfig(BaseModel):
    version: int = CONFIG_VERSION
    trakt: TraktConfig = Field(default_factory=TraktConfig)
    radarr: RadarrConfig = Field(default_factory=RadarrConfig)
    sonarr: SonarrConfig = Field(default_factory=SonarrConfig)
    lists: list[ListJob] = Field(default_factory=list)
    auth: AuthConfig = Field(default_factory=AuthConfig)

    def find_list(self, list_id: str) -> ListJob | None:
        return next((item for item in self.lists if item.id == list_id), None)


# --------------------------------------------------------------------------- #
# Store
# --------------------------------------------------------------------------- #


class ConfigStore:
    """Loads, holds and atomically persists the app config."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path else settings.CONFIG_FILE
        self._lock = threading.RLock()
        self._config: AppConfig | None = None

    # -- loading ----------------------------------------------------------- #

    def load(self) -> AppConfig:
        with self._lock:
            raw: dict[str, Any] = {}
            if self.path.exists():
                try:
                    raw = json.loads(self.path.read_text("utf-8"))
                except (OSError, ValueError) as exc:
                    raise RuntimeError(
                        f"Could not read config at {self.path}: {exc}. "
                        "Fix or remove the file and restart."
                    ) from exc

            first_run = not raw
            self._config = AppConfig.model_validate(raw)

            # Seed the password from the environment only when none is set yet,
            # so a stale env var can never silently reset a chosen password.
            if settings.ENV_PASSWORD and not self._config.auth.password_hash:
                self._config.auth.password_hash = hash_password(settings.ENV_PASSWORD)
                log.info("Web password initialised from PLEXTRA_PASSWORD.")

            self.save()
            if first_run:
                log.info("Created a fresh config at %s", self.path)
            return self._config

    @property
    def config(self) -> AppConfig:
        if self._config is None:
            return self.load()
        return self._config

    # -- persistence ------------------------------------------------------- #

    def save(self) -> None:
        with self._lock:
            if self._config is None:
                return
            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload = self._config.model_dump(mode="json")
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), "utf-8")
            # The file holds API keys and OAuth tokens in the clear, same as
            # Radarr/Sonarr's own config, so keep it owner-readable only.
            try:
                os.chmod(tmp, 0o600)
            except OSError:
                pass
            tmp.replace(self.path)

    def mutate(self, mutator) -> AppConfig:
        """Apply ``mutator(config)`` under the lock, then persist."""
        with self._lock:
            config = self.config
            mutator(config)
            self.save()
            return config


store = ConfigStore()
