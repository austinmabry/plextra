"""Point Plextra at a scratch config dir before anything imports its settings."""

import os
import tempfile

os.environ.setdefault("PLEXTRA_CONFIG_DIR", tempfile.mkdtemp(prefix="plextra-tests-"))
os.environ.setdefault("PLEXTRA_ADD_DELAY", "0")
os.environ.pop("PLEXTRA_PASSWORD", None)

import pytest  # noqa: E402


@pytest.fixture
def store(tmp_path):
    from plextra.config import ConfigStore

    config_store = ConfigStore(tmp_path / "config.json")
    config_store.load()
    return config_store


@pytest.fixture
def database(tmp_path):
    from plextra.db import Database

    database = Database(tmp_path / "plextra.db")
    database.init()
    return database


@pytest.fixture
def client(tmp_path):
    """A TestClient with the module-level singletons pointed at tmp_path."""
    from fastapi.testclient import TestClient

    from plextra import api as api_module
    from plextra.config import store as global_store
    from plextra.db import db as global_db

    global_store.path = tmp_path / "config.json"
    global_store._config = None
    global_db.path = tmp_path / "plextra.db"

    with TestClient(api_module.app) as test_client:
        yield test_client
