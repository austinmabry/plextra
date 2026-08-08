"""FastAPI application: REST API plus the static web GUI."""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import threading
import time
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Body, Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import __version__, providers, settings
from .clients import ArrError, RadarrClient, SonarrClient, TraktClient, TraktError
from .config import (
    AppConfig,
    ListJob,
    MdblistConfig,
    PacingConfig,
    PlexConfig,
    RadarrConfig,
    SonarrConfig,
    TmdbConfig,
    TraktAccount,
    hash_password,
    store,
    verify_password,
)
from .providers.base import ProviderError
from .db import db
from .logbuf import ring_handler
from .scheduler import SyncScheduler
from .sync import SyncAlreadyRunning, SyncConfigError, SyncEngine

log = logging.getLogger("sidecarr.api")

COOKIE_NAME = "sidecarr_session"
SESSION_MAX_AGE = 30 * 24 * 60 * 60

CSRF_COOKIE = "sidecarr_csrf"
CSRF_HEADER = "x-csrf-token"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

# Fetch metadata. Browsers set this themselves and scripts cannot override it,
# so "same-origin" is proof the request did not come from another site. It is a
# second, independent proof alongside the double-submit token: a page cached
# from before the token existed still passes, while a cross-site page - which
# gets "cross-site" - still does not.
FETCH_SITE_HEADER = "sec-fetch-site"
SAME_SITE_VALUES = frozenset({"same-origin", "none"})

# 'unsafe-inline' is needed for style only: the markup carries a few inline
# style attributes. All script lives in static files, so script-src stays strict.
CONTENT_SECURITY_POLICY = (
    "default-src 'self'; "
    "img-src 'self' data:; "
    "style-src 'self' 'unsafe-inline'; "
    "script-src 'self'; "
    "connect-src 'self'; "
    "form-action 'self'; "
    "base-uri 'none'; "
    "frame-ancestors 'none'"
)

engine: SyncEngine
scheduler: SyncScheduler


# --------------------------------------------------------------------------- #
# Session handling
# --------------------------------------------------------------------------- #


def _sign(value: str, secret: str) -> str:
    return hmac.new(secret.encode(), value.encode(), hashlib.sha256).hexdigest()


def make_session(secret: str) -> str:
    issued = str(int(time.time()))
    return f"{issued}.{_sign(issued, secret)}"


def valid_session(token: str | None, secret: str) -> bool:
    if not token or "." not in token:
        return False
    issued, signature = token.rsplit(".", 1)
    if not hmac.compare_digest(signature, _sign(issued, secret)):
        return False
    try:
        return time.time() - int(issued) < SESSION_MAX_AGE
    except ValueError:
        return False


def require_auth(request: Request) -> None:
    config = store.config
    if not config.auth.enabled:
        return
    if not valid_session(request.cookies.get(COOKIE_NAME), config.auth.secret_key):
        raise HTTPException(status_code=401, detail="Authentication required.")


Auth = Depends(require_auth)


# --------------------------------------------------------------------------- #
# Request models
# --------------------------------------------------------------------------- #


class LoginRequest(BaseModel):
    password: str


class PasswordRequest(BaseModel):
    password: str = ""


class TraktAppRequest(BaseModel):
    client_id: str = ""
    client_secret: str = ""


class DevicePollRequest(BaseModel):
    device_code: str


class ConnectionTestRequest(BaseModel):
    url: str = ""
    api_key: str = ""


# --------------------------------------------------------------------------- #
# App
# --------------------------------------------------------------------------- #


@asynccontextmanager
async def lifespan(app: FastAPI):
    global engine, scheduler

    settings.warn_about_legacy_env()
    settings.migrate_legacy_paths()
    store.load()
    db.init()
    engine = SyncEngine(store, db)
    scheduler = SyncScheduler(store, engine, db)
    scheduler.start()

    log.info("Sidecarr %s is listening on %s:%s", __version__, settings.HOST, settings.PORT)
    if not store.config.auth.enabled:
        log.warning(
            "No web password is set. Anyone who can reach this port can read the "
            "credentials for Radarr, Sonarr and every list provider. Set one in "
            "Settings."
        )
    try:
        yield
    finally:
        scheduler.shutdown()


app = FastAPI(title="Sidecarr", version=__version__, lifespan=lifespan)


def same_origin(request: Request) -> bool:
    """True when the browser itself vouches that this is not a cross-site request.

    Scripts cannot set Sec-Fetch-Site - it is a forbidden header name - so a page
    on another origin cannot claim "same-origin". Browsers too old to send it at
    all leave the header absent, and those fall back to the token.
    """
    site = request.headers.get(FETCH_SITE_HEADER, "").strip().lower()
    return site in SAME_SITE_VALUES


@app.middleware("http")
async def security(request: Request, call_next):
    """Double-submit CSRF protection, plus the usual response headers.

    Everything here mutates something real - adding titles to a library,
    rewriting credentials - and it is authenticated by a cookie, so a page on
    another site could otherwise drive it just by knowing the port. The token
    lives in a readable cookie that the UI echoes back in a header; a
    cross-origin page can send the cookie but cannot read it, so it cannot
    produce the header.
    """
    token = request.cookies.get(CSRF_COOKIE, "")

    if request.method not in SAFE_METHODS and not same_origin(request):
        sent = request.headers.get(CSRF_HEADER, "")
        if not token or not sent or not hmac.compare_digest(sent, token):
            return JSONResponse(
                status_code=403,
                content={
                    "detail": (
                        "Missing or invalid CSRF token. Force-reload the page - after "
                        "an upgrade the browser can still be running the previous "
                        "version from cache. Scripted clients should read the "
                        "sidecarr_csrf cookie from any GET and send it back in the "
                        "X-CSRF-Token header."
                    )
                },
            )

    response = await call_next(request)

    if not token:
        response.set_cookie(
            CSRF_COOKIE,
            secrets.token_urlsafe(32),
            max_age=SESSION_MAX_AGE,
            httponly=False,  # the UI has to read it
            samesite="lax",
            secure=settings.COOKIE_SECURE,
        )

    # Assets are requested with a content hash, so revalidating costs one 304 and
    # guarantees an upgrade is picked up. Safari in particular will otherwise
    # reuse a script for days without asking.
    if request.url.path.startswith("/static/"):
        response.headers.setdefault("Cache-Control", "no-cache")

    response.headers.setdefault("Content-Security-Policy", CONTENT_SECURITY_POLICY)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    return response


@app.exception_handler(SyncConfigError)
async def _sync_config_error(_: Request, exc: SyncConfigError) -> JSONResponse:
    return JSONResponse(status_code=400, content={"detail": str(exc)})


# --------------------------------------------------------------------------- #
# Health and auth
# --------------------------------------------------------------------------- #


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "version": __version__}


@app.get("/api/auth/status")
def auth_status(request: Request) -> dict[str, Any]:
    config = store.config
    return {
        "auth_required": config.auth.enabled,
        "authenticated": (
            not config.auth.enabled
            or valid_session(request.cookies.get(COOKIE_NAME), config.auth.secret_key)
        ),
    }


@app.post("/api/auth/login")
def login(payload: LoginRequest, response: Response) -> dict[str, Any]:
    config = store.config
    if not config.auth.enabled:
        return {"authenticated": True}
    if not verify_password(payload.password, config.auth.password_hash):
        raise HTTPException(status_code=401, detail="Wrong password.")
    response.set_cookie(
        COOKIE_NAME,
        make_session(config.auth.secret_key),
        max_age=SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=settings.COOKIE_SECURE,
    )
    return {"authenticated": True}


@app.post("/api/auth/logout")
def logout(response: Response) -> dict[str, Any]:
    response.delete_cookie(COOKIE_NAME)
    return {"authenticated": False}


@app.put("/api/auth/password", dependencies=[Auth])
def set_password(payload: PasswordRequest, response: Response) -> dict[str, Any]:
    password = payload.password.strip()

    def apply(config: AppConfig) -> None:
        config.auth.password_hash = hash_password(password) if password else ""

    store.mutate(apply)
    if password:
        response.set_cookie(
            COOKIE_NAME,
            make_session(store.config.auth.secret_key),
            max_age=SESSION_MAX_AGE,
            httponly=True,
            samesite="lax",
            secure=settings.COOKIE_SECURE,
        )
        log.info("Web password updated.")
    else:
        log.warning("Web password removed; the GUI is now open to anyone on this port.")
    return {"auth_required": bool(password)}


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #


def _public_config(config: AppConfig) -> dict[str, Any]:
    data = config.model_dump(mode="json")
    # Tokens and hashes never leave the server.
    data["auth"] = {"enabled": config.auth.enabled}
    data["trakt"]["accounts"] = sorted(config.trakt.accounts)
    return data


@app.get("/api/config", dependencies=[Auth])
def get_config() -> dict[str, Any]:
    return _public_config(store.config)


@app.put("/api/config/trakt", dependencies=[Auth])
def update_trakt(payload: TraktAppRequest) -> dict[str, Any]:
    def apply(config: AppConfig) -> None:
        config.trakt.client_id = payload.client_id.strip()
        config.trakt.client_secret = payload.client_secret.strip()

    store.mutate(apply)
    return _public_config(store.config)


@app.put("/api/config/radarr", dependencies=[Auth])
def update_radarr(payload: RadarrConfig) -> dict[str, Any]:
    store.mutate(lambda config: setattr(config, "radarr", payload))
    return _public_config(store.config)


@app.put("/api/config/sonarr", dependencies=[Auth])
def update_sonarr(payload: SonarrConfig) -> dict[str, Any]:
    store.mutate(lambda config: setattr(config, "sonarr", payload))
    return _public_config(store.config)


@app.put("/api/config/tmdb", dependencies=[Auth])
def update_tmdb(payload: TmdbConfig) -> dict[str, Any]:
    store.mutate(lambda config: setattr(config, "tmdb", payload))
    return _public_config(store.config)


@app.put("/api/config/mdblist", dependencies=[Auth])
def update_mdblist(payload: MdblistConfig) -> dict[str, Any]:
    store.mutate(lambda config: setattr(config, "mdblist", payload))
    return _public_config(store.config)


@app.put("/api/config/plex", dependencies=[Auth])
def update_plex(payload: PlexConfig) -> dict[str, Any]:
    store.mutate(lambda config: setattr(config, "plex", payload))
    return _public_config(store.config)


# --------------------------------------------------------------------------- #
# Providers
# --------------------------------------------------------------------------- #


@app.get("/api/providers", dependencies=[Auth])
def list_providers() -> dict[str, Any]:
    """Everything the list editor needs to render every source, dynamically."""
    return {"providers": providers.describe_all(store.config)}


@app.post("/api/providers/{key}/test", dependencies=[Auth])
def test_provider(key: str) -> dict[str, Any]:
    try:
        provider = providers.build(key, store.config)
    except ProviderError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    try:
        with provider:
            return {"ok": bool(provider.validate()), "provider": provider.name}
    except ProviderError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/providers/{key}/lists", dependencies=[Auth])
def provider_lists(key: str, account: str = "") -> dict[str, Any]:
    """The list picker, for providers that can enumerate a user's own lists."""
    config = store.config
    if key == "trakt":
        username = account or config.trakt.default_account()
        if not username:
            raise HTTPException(status_code=400, detail="Connect a Trakt account first.")
        try:
            with TraktClient(config.trakt) as trakt:
                return {"account": username, "lists": trakt.user_lists(username)}
        except TraktError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    if key == "mdblist":
        try:
            with providers.build("mdblist", config) as provider:
                return {"account": "", "lists": provider.my_lists()}
        except ProviderError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    raise HTTPException(status_code=400, detail=f"{key} cannot list your lists.")


# --------------------------------------------------------------------------- #
# Trakt
# --------------------------------------------------------------------------- #


@app.post("/api/trakt/device/start", dependencies=[Auth])
def trakt_device_start() -> dict[str, Any]:
    try:
        with TraktClient(store.config.trakt) as trakt:
            return trakt.device_code()
    except TraktError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/trakt/device/poll", dependencies=[Auth])
def trakt_device_poll(payload: DevicePollRequest) -> dict[str, Any]:
    try:
        with TraktClient(store.config.trakt) as trakt:
            status, token = trakt.device_token(payload.device_code)
            if status != "ok" or not token:
                return {"status": status}

            username = trakt.whoami(token["access_token"])
            account = TraktAccount.model_validate(token)
            store.mutate(
                lambda config: config.trakt.accounts.__setitem__(username, account)
            )
            log.info("Connected the Trakt account %s.", username)
            return {"status": "ok", "username": username}
    except TraktError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete("/api/trakt/accounts/{username}", dependencies=[Auth])
def trakt_remove_account(username: str) -> dict[str, Any]:
    def apply(config: AppConfig) -> None:
        config.trakt.accounts.pop(username, None)

    store.mutate(apply)
    log.info("Removed the Trakt account %s.", username)
    return _public_config(store.config)


@app.get("/api/trakt/lists", dependencies=[Auth])
def trakt_lists(account: str = "") -> dict[str, Any]:
    config = store.config
    username = account or config.trakt.default_account()
    if not username:
        raise HTTPException(status_code=400, detail="Connect a Trakt account first.")
    try:
        with TraktClient(config.trakt) as trakt:
            return {"account": username, "lists": trakt.user_lists(username)}
    except TraktError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/trakt/test", dependencies=[Auth])
def trakt_test() -> dict[str, Any]:
    try:
        with TraktClient(store.config.trakt) as trakt:
            return {"ok": trakt.validate()}
    except TraktError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# --------------------------------------------------------------------------- #
# Radarr / Sonarr
# --------------------------------------------------------------------------- #


@app.post("/api/radarr/test", dependencies=[Auth])
def radarr_test(payload: ConnectionTestRequest) -> dict[str, Any]:
    config = store.config.radarr
    try:
        with RadarrClient(
            payload.url or config.url,
            payload.api_key or config.api_key,
            timeout=15.0,
            retries=1,
        ) as arr:
            return arr.test()
    except ArrError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/sonarr/test", dependencies=[Auth])
def sonarr_test(payload: ConnectionTestRequest) -> dict[str, Any]:
    config = store.config.sonarr
    try:
        with SonarrClient(
            payload.url or config.url,
            payload.api_key or config.api_key,
            timeout=15.0,
            retries=1,
        ) as arr:
            result = arr.test()
            result["language_profiles"] = [
                {"id": p["id"], "name": p["name"]} for p in arr.language_profiles()
            ]
            return result
    except ArrError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# --------------------------------------------------------------------------- #
# Lists
# --------------------------------------------------------------------------- #


@app.get("/api/lists", dependencies=[Auth])
def list_jobs() -> dict[str, Any]:
    next_runs = scheduler.next_runs()
    running = engine.running_lists()
    pending = db.queue_counts()
    return {
        "lists": [
            {
                **job.model_dump(mode="json"),
                "next_run": next_runs.get(job.id),
                "running": job.id in running,
                "queued": pending.get(job.id, 0),
                "last_run": db.recent_runs(limit=1, list_id=job.id),
            }
            for job in store.config.lists
        ]
    }


def _validate_source(job: ListJob) -> None:
    """Reject a list whose provider/source/media combination cannot work.

    Better a 422 while editing than a failed run at 3am.
    """
    try:
        provider = providers.build(job.source.provider, store.config)
    except ProviderError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    source = provider.source(job.source.type)
    if source is None:
        known = ", ".join(s.key for s in provider.source_types)
        raise HTTPException(
            status_code=422,
            detail=f"{provider.name} has no source {job.source.type!r}. Known: {known}.",
        )
    if job.media_type not in source.media:
        kind = "movies" if job.media_type == "movie" else "shows"
        raise HTTPException(
            status_code=422,
            detail=f"{provider.name}'s {source.label!r} source does not support {kind}.",
        )


@app.post("/api/lists", dependencies=[Auth])
def create_list(payload: ListJob) -> dict[str, Any]:
    _validate_source(payload)
    store.mutate(lambda config: config.lists.append(payload))
    scheduler.reload()
    log.info("Created the list %r.", payload.name)
    return payload.model_dump(mode="json")


@app.put("/api/lists/{list_id}", dependencies=[Auth])
def update_list(list_id: str, payload: ListJob) -> dict[str, Any]:
    if store.config.find_list(list_id) is None:
        raise HTTPException(status_code=404, detail="No such list.")

    payload.id = list_id
    _validate_source(payload)

    def apply(config: AppConfig) -> None:
        config.lists = [payload if job.id == list_id else job for job in config.lists]

    store.mutate(apply)
    scheduler.reload()
    return payload.model_dump(mode="json")


@app.delete("/api/lists/{list_id}", dependencies=[Auth])
def delete_list(list_id: str) -> dict[str, Any]:
    if store.config.find_list(list_id) is None:
        raise HTTPException(status_code=404, detail="No such list.")

    def apply(config: AppConfig) -> None:
        config.lists = [job for job in config.lists if job.id != list_id]

    store.mutate(apply)
    dropped = db.queue_clear(list_id)
    scheduler.reload()
    log.info(
        "Deleted the list %s%s.",
        list_id,
        f" and {dropped} of its queued titles" if dropped else "",
    )
    return {"deleted": list_id, "queued_dropped": dropped}


@app.post("/api/lists/{list_id}/run", dependencies=[Auth])
def run_list(list_id: str, dry_run: bool = False) -> JSONResponse:
    job = store.config.find_list(list_id)
    if job is None:
        raise HTTPException(status_code=404, detail="No such list.")
    if engine.is_running(list_id):
        raise HTTPException(status_code=409, detail="That list is already syncing.")

    def worker() -> None:
        try:
            engine.run(list_id, dry_run=dry_run or None)
        except SyncAlreadyRunning:
            pass
        except Exception:  # pragma: no cover - engine logs the detail
            log.exception("Manual run of %r failed.", job.name)

    threading.Thread(target=worker, name=f"sync-{list_id[:8]}", daemon=True).start()
    return JSONResponse(status_code=202, content={"status": "started", "list_id": list_id})


# --------------------------------------------------------------------------- #
# Runs, logs, status
# --------------------------------------------------------------------------- #


class SchedulerRequest(BaseModel):
    paused: bool


@app.put("/api/scheduler", dependencies=[Auth])
def set_scheduler(payload: SchedulerRequest) -> dict[str, Any]:
    """Pause or resume every schedule at once, for maintenance windows."""
    store.mutate(lambda config: setattr(config.scheduler, "paused", payload.paused))
    log.info("Scheduler %s.", "paused" if payload.paused else "resumed")
    return {"paused": payload.paused}


class PacingRequest(BaseModel):
    enabled: bool = False
    max_adds: int = 10
    window_minutes: int = 10


@app.get("/api/pacing", dependencies=[Auth])
def get_pacing() -> dict[str, Any]:
    config = store.config
    allowance = engine.allowance(config)
    return {
        **config.pacing.model_dump(mode="json"),
        "queued": sum(db.queue_counts().values()),
        "queued_by_list": db.queue_counts(),
        "allowance": None if allowance < 0 else allowance,
        "used_in_window": db.adds_since(config.pacing.window_minutes * 60),
    }


@app.put("/api/pacing", dependencies=[Auth])
def set_pacing(payload: PacingRequest) -> dict[str, Any]:
    def apply(config: AppConfig) -> None:
        config.pacing = PacingConfig(**payload.model_dump())

    store.mutate(apply)
    log.info(
        "Rate limit %s (%d titles per %d min).",
        "on" if payload.enabled else "off",
        payload.max_adds,
        payload.window_minutes,
    )
    return get_pacing()


@app.delete("/api/pacing/queue", dependencies=[Auth])
def clear_queue(list_id: str = "") -> dict[str, Any]:
    dropped = db.queue_clear(list_id or None)
    log.info("Cleared %d queued titles.", dropped)
    return {"dropped": dropped}


@app.get("/api/runs", dependencies=[Auth])
def runs(limit: int = 25, list_id: str = "") -> dict[str, Any]:
    return {"runs": db.recent_runs(limit=min(limit, 200), list_id=list_id or None)}


@app.get("/api/runs/{run_id}/items", dependencies=[Auth])
def run_items(run_id: int, limit: int = 500) -> dict[str, Any]:
    return {"items": db.run_items(run_id, limit=min(limit, 2000))}


@app.get("/api/logs", dependencies=[Auth])
def logs(after: int = 0, limit: int = 400) -> dict[str, Any]:
    entries = ring_handler.tail(after=after, limit=min(limit, 2000))
    return {"logs": entries, "cursor": entries[-1]["seq"] if entries else after}


@app.get("/api/status", dependencies=[Auth])
def status() -> dict[str, Any]:
    config = store.config
    return {
        "version": __version__,
        "trakt": {
            "configured": config.trakt.configured,
            "accounts": sorted(config.trakt.accounts),
        },
        "radarr": {"enabled": config.radarr.enabled, "configured": config.radarr.configured},
        "sonarr": {"enabled": config.sonarr.enabled, "configured": config.sonarr.configured},
        "providers": [
            {"key": p.key, "name": p.name, "configured": p.configured()}
            for p in providers.build_all(config)
        ],
        "lists": {
            "total": len(config.lists),
            "enabled": sum(1 for job in config.lists if job.enabled),
        },
        "running": sorted(engine.running_lists()),
        "scheduler_paused": config.scheduler.paused,
        "pacing": {
            "enabled": config.pacing.enabled,
            "max_adds": config.pacing.max_adds,
            "window_minutes": config.pacing.window_minutes,
            "queued": sum(db.queue_counts().values()),
        },
        "next_runs": scheduler.next_runs(),
        "totals": db.totals(),
        "recent_runs": db.recent_runs(limit=10),
    }


# --------------------------------------------------------------------------- #
# Static GUI
# --------------------------------------------------------------------------- #

app.mount(
    "/static", StaticFiles(directory=settings.WEB_DIR / "static"), name="static"
)

STATIC_ASSETS = ("app.js", "style.css")


def asset_version() -> str:
    """A short hash of the front-end assets, used to bust browser caches.

    An upgrade that changes app.js while the browser keeps serving the previous
    one from cache leaves the UI talking to a server it no longer matches - which
    is exactly how 0.3.0 broke every mutating request for anyone who had loaded
    0.2.x. Content-addressed URLs make that impossible.
    """
    digest = hashlib.sha256()
    for name in STATIC_ASSETS:
        digest.update((settings.WEB_DIR / "static" / name).read_bytes())
    return digest.hexdigest()[:12]


def build_index() -> str:
    html = (settings.WEB_DIR / "index.html").read_text()
    version = asset_version()
    for name in STATIC_ASSETS:
        html = html.replace(f"/static/{name}", f"/static/{name}?v={version}")
    return html


_index_html: str | None = None


@app.get("/")
def index() -> HTMLResponse:
    global _index_html
    if _index_html is None:
        _index_html = build_index()
    # The page names the exact asset versions it needs, so it must never itself
    # be served from cache.
    return HTMLResponse(_index_html, headers={"Cache-Control": "no-store"})


@app.get("/favicon.svg")
def favicon() -> FileResponse:
    return FileResponse(settings.WEB_DIR / "static" / "favicon.svg")
