"""Point Sidecarr at a scratch config dir before anything imports its settings."""

import os
import tempfile

os.environ.setdefault("SIDECARR_CONFIG_DIR", tempfile.mkdtemp(prefix="sidecarr-tests-"))
os.environ.setdefault("SIDECARR_ADD_DELAY", "0")
os.environ.pop("SIDECARR_PASSWORD", None)

import pytest  # noqa: E402


@pytest.fixture
def store(tmp_path):
    from sidecarr.config import ConfigStore

    config_store = ConfigStore(tmp_path / "config.json")
    config_store.load()
    return config_store


@pytest.fixture
def database(tmp_path):
    from sidecarr.db import Database

    database = Database(tmp_path / "sidecarr.db")
    database.init()
    return database


@pytest.fixture
def client(tmp_path):
    """A TestClient with the module-level singletons pointed at tmp_path."""
    from fastapi.testclient import TestClient

    from sidecarr import api as api_module
    from sidecarr.config import store as global_store
    from sidecarr.db import db as global_db

    global_store.path = tmp_path / "config.json"
    global_store._config = None
    global_db.path = tmp_path / "sidecarr.db"

    with TestClient(api_module.app) as test_client:
        # Do what the browser does: take the CSRF cookie from a GET and echo it
        # back in the header on every mutating request.
        test_client.get("/api/health")
        test_client.headers["X-CSRF-Token"] = test_client.cookies.get("sidecarr_csrf", "")
        yield test_client


@pytest.fixture
def raw_client(tmp_path):
    """A client that does *not* send the CSRF header, for testing the guard."""
    from fastapi.testclient import TestClient

    from sidecarr import api as api_module
    from sidecarr.config import store as global_store
    from sidecarr.db import db as global_db

    global_store.path = tmp_path / "config.json"
    global_store._config = None
    global_db.path = tmp_path / "sidecarr.db"

    with TestClient(api_module.app) as test_client:
        yield test_client
