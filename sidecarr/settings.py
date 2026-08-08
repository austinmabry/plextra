"""Runtime paths and environment-derived settings."""

from __future__ import annotations

import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)

# The project was called Plextra through 0.2.0. Anyone upgrading still has
# PLEXTRA_* variables in their compose file, and PLEXTRA_SECRET_KEY in
# particular is load-bearing: drop it and stored credentials stop decrypting.
LEGACY_PREFIX = "PLEXTRA_"
_legacy_seen: list[str] = []


def _env(name: str) -> str | None:
    """Read SIDECARR_<name>, falling back to the retired PLEXTRA_<name>."""
    value = os.environ.get(f"SIDECARR_{name}")
    if value is not None:
        return value
    legacy = os.environ.get(f"{LEGACY_PREFIX}{name}")
    if legacy is not None:
        _legacy_seen.append(f"{LEGACY_PREFIX}{name}")
    return legacy


def _str_env(name: str, default: str) -> str:
    value = _env(name)
    return default if value is None else value


def _bool_env(name: str, default: bool) -> bool:
    raw = _env(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def warn_about_legacy_env() -> None:
    """Log once, at startup, if anything is still using the old prefix."""
    if _legacy_seen:
        log.warning(
            "Using deprecated %s environment variables: %s. "
            "Rename them to SIDECARR_* - the old names will be removed in a future release.",
            LEGACY_PREFIX.rstrip("_"),
            ", ".join(sorted(set(_legacy_seen))),
        )


CONFIG_DIR = Path(_str_env("CONFIG_DIR", "/config"))
CONFIG_FILE = CONFIG_DIR / "config.json"
DB_FILE = CONFIG_DIR / "sidecarr.db"

# Pre-0.4.0 database, renamed on startup so run history survives the rebrand.
LEGACY_DB_FILE = CONFIG_DIR / "plextra.db"

HOST = _str_env("HOST", "0.0.0.0")
PORT = int(_str_env("PORT", "9898"))
LOG_LEVEL = _str_env("LOG_LEVEL", "INFO").upper()

# Optional password seeded on first boot. Once the config has a password hash,
# this is ignored unless the stored hash is empty.
ENV_PASSWORD = _env("PASSWORD") or None

# Passphrase for encrypting stored credentials. When set, the key never touches
# the config volume. When unset, a random key is generated into /config.
SECRET_KEY = _env("SECRET_KEY") or None

# Session cookie is marked Secure only when Sidecarr is served over HTTPS.
COOKIE_SECURE = _bool_env("COOKIE_SECURE", False)

WEB_DIR = Path(__file__).parent / "web"

# How many Trakt pages (100 items each) a single sync will pull.
MAX_TRAKT_PAGES = int(_str_env("MAX_TRAKT_PAGES", "20"))

# Seconds to wait between consecutive add requests to Radarr/Sonarr.
ADD_DELAY_SECONDS = float(_str_env("ADD_DELAY", "0.5"))

# Titles per bulk add. Radarr and Sonarr resolve a batch against their metadata
# service in one go, which is far kinder than one request per title.
BULK_BATCH_SIZE = int(_str_env("BULK_BATCH_SIZE", "50"))


def migrate_legacy_paths() -> None:
    """Adopt a pre-rebrand database so upgrades keep their run history."""
    if DB_FILE.exists() or not LEGACY_DB_FILE.exists():
        return
    try:
        for suffix in ("", "-wal", "-shm"):
            source = LEGACY_DB_FILE.with_name(LEGACY_DB_FILE.name + suffix)
            if source.exists():
                source.rename(DB_FILE.with_name(DB_FILE.name + suffix))
    except OSError as exc:  # pragma: no cover - depends on volume permissions
        log.warning("Could not migrate %s to %s: %s", LEGACY_DB_FILE, DB_FILE, exc)
        return
    log.info("Migrated %s to %s", LEGACY_DB_FILE.name, DB_FILE.name)


# How long a title may sit in the paced-add queue before it is given up on. It
# will be re-queued by the next sync if it is still on its list.
QUEUE_TTL_DAYS = int(_str_env("QUEUE_TTL_DAYS", "30"))
