"""Runtime paths and environment-derived settings."""

from __future__ import annotations

import os
from pathlib import Path


def _bool_env(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


CONFIG_DIR = Path(os.environ.get("PLEXTRA_CONFIG_DIR", "/config"))
CONFIG_FILE = CONFIG_DIR / "config.json"
DB_FILE = CONFIG_DIR / "plextra.db"

HOST = os.environ.get("PLEXTRA_HOST", "0.0.0.0")
PORT = int(os.environ.get("PLEXTRA_PORT", "9898"))
LOG_LEVEL = os.environ.get("PLEXTRA_LOG_LEVEL", "INFO").upper()

# Optional password seeded on first boot. Once the config has a password hash,
# this is ignored unless the stored hash is empty.
ENV_PASSWORD = os.environ.get("PLEXTRA_PASSWORD") or None

# Passphrase for encrypting stored credentials. When set, the key never touches
# the config volume. When unset, a random key is generated into /config.
SECRET_KEY = os.environ.get("PLEXTRA_SECRET_KEY") or None

# Session cookie is marked Secure only when Plextra is served over HTTPS.
COOKIE_SECURE = _bool_env("PLEXTRA_COOKIE_SECURE", False)

WEB_DIR = Path(__file__).parent / "web"

# How many Trakt pages (100 items each) a single sync will pull.
MAX_TRAKT_PAGES = int(os.environ.get("PLEXTRA_MAX_TRAKT_PAGES", "20"))

# Seconds to wait between consecutive add requests to Radarr/Sonarr.
ADD_DELAY_SECONDS = float(os.environ.get("PLEXTRA_ADD_DELAY", "0.5"))

# Titles per bulk add. Radarr and Sonarr resolve a batch against their metadata
# service in one go, which is far kinder than one request per title.
BULK_BATCH_SIZE = int(os.environ.get("PLEXTRA_BULK_BATCH_SIZE", "50"))
