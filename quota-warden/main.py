#!/usr/bin/env python3
"""quota-warden — data-volume request quotas for Jellyseerr.

Jellyseerr natively enforces COUNT quotas (movies per N days, seasons per
N days). This sidecar adds the missing dimension: MB per user per rolling
window. Every cycle it:

  1. computes each user's actual data usage: bytes on disk (via Radarr/
     Sonarr) for media that user requested inside the window,
  2. walks PENDING requests oldest-first and auto-declines any that would
     push the user past their budget (using size estimates for content that
     hasn't downloaded yet).

Approve/decline of everything else stays in Jellyseerr's normal flow.
Config: /etc/quota-warden/quotas.yml (see quotas.yml in the repo).
"""

import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

import yaml

log = logging.getLogger("quota-warden")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

JELLYSEERR_URL = os.environ["JELLYSEERR_URL"].rstrip("/")
JELLYSEERR_API_KEY = os.environ["JELLYSEERR_API_KEY"]
RADARR_URL = os.environ.get("RADARR_URL", "").rstrip("/")
RADARR_API_KEY = os.environ.get("RADARR_API_KEY", "")
SONARR_URL = os.environ.get("SONARR_URL", "").rstrip("/")
SONARR_API_KEY = os.environ.get("SONARR_API_KEY", "")
INTERVAL = int(os.environ.get("CHECK_INTERVAL_SECONDS", "300"))
QUOTA_FILE = os.environ.get("QUOTA_FILE", "/etc/quota-warden/quotas.yml")

STATUS_PENDING = 1
STATUS_APPROVED = 2

MB = 1024 * 1024


def api(base: str, path: str, key: str, method: str = "GET", header: str = "X-Api-Key"):
    req = urllib.request.Request(base + path, method=method)
    req.add_header(header, key)
    req.add_header("Accept", "application/json")
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = resp.read()
    return json.loads(body) if body else None


def jellyseerr(path: str, method: str = "GET"):
    return api(JELLYSEERR_URL, "/api/v1" + path, JELLYSEERR_API_KEY, method)


def load_config() -> dict:
    with open(QUOTA_FILE) as f:
        cfg = yaml.safe_load(f) or {}
    cfg.setdefault("window_days", 7)
    cfg.setdefault("default_quota_mb", 0)  # 0 = unlimited
    cfg.setdefault("estimate_movie_mb", 8192)
    cfg.setdefault("estimate_season_mb", 20480)
    cfg.setdefault("users", {}) or {}
    return cfg


def quota_for(cfg: dict, user: str) -> int:
    users = cfg.get("users") or {}
    return int(users.get(user, cfg["default_quota_mb"]))


def fetch_requests() -> list:
    """All requests, newest API pages first; returns a flat list."""
    out, skip = [], 0
    while True:
        page = jellyseerr(f"/request?take=100&skip={skip}&sort=added")
        results = page.get("results", [])
        out.extend(results)
        skip += len(results)
        if skip >= page.get("pageInfo", {}).get("results", 0) or not results:
            return out


def requester_name(req: dict) -> str:
    by = req.get("requestedBy") or {}
    return by.get("jellyfinUsername") or by.get("displayName") or f"user-{by.get('id')}"


def parse_when(req: dict) -> datetime:
    raw = (req.get("createdAt") or "1970-01-01T00:00:00.000Z").replace("Z", "+00:00")
    return datetime.fromisoformat(raw)


def radarr_sizes() -> dict:
    """tmdbId -> bytes on disk."""
    if not RADARR_URL:
        return {}
    try:
        movies = api(RADARR_URL, "/api/v3/movie", RADARR_API_KEY)
        return {m["tmdbId"]: m.get("sizeOnDisk", 0) for m in movies}
    except Exception as e:  # noqa: BLE001 - keep enforcing with estimates
        log.warning("Radarr unreachable (%s); falling back to size estimates", e)
        return {}


def sonarr_sizes() -> dict:
    """tvdbId -> {seasonNumber: bytes on disk}."""
    if not SONARR_URL:
        return {}
    try:
        series = api(SONARR_URL, "/api/v3/series", SONARR_API_KEY)
        return {
            s["tvdbId"]: {
                sea["seasonNumber"]: (sea.get("statistics") or {}).get("sizeOnDisk", 0)
                for sea in s.get("seasons", [])
            }
            for s in series
        }
    except Exception as e:  # noqa: BLE001
        log.warning("Sonarr unreachable (%s); falling back to size estimates", e)
        return {}


def request_mb(req: dict, cfg: dict, movies: dict, shows: dict) -> float:
    """Size of a request in MB: actual bytes when downloaded, estimate otherwise."""
    media = req.get("media") or {}
    if req.get("type") == "movie":
        actual = movies.get(media.get("tmdbId"), 0)
        return actual / MB if actual > 0 else cfg["estimate_movie_mb"]
    seasons = [s.get("seasonNumber") for s in (req.get("seasons") or [])]
    per_season = shows.get(media.get("tvdbId"), {})
    total = 0.0
    for num in seasons:
        actual = per_season.get(num, 0)
        total += actual / MB if actual > 0 else cfg["estimate_season_mb"]
    return total


def enforce_once() -> None:
    cfg = load_config()
    window_start = datetime.now(timezone.utc) - timedelta(days=cfg["window_days"])
    requests = fetch_requests()
    movies, shows = radarr_sizes(), sonarr_sizes()

    # Current usage per user: approved/available requests inside the window.
    usage: dict[str, float] = {}
    for req in requests:
        if req.get("status") == STATUS_APPROVED and parse_when(req) >= window_start:
            user = requester_name(req)
            usage[user] = usage.get(user, 0.0) + request_mb(req, cfg, movies, shows)

    # Walk pending requests oldest-first; decline what busts the budget.
    pending = sorted(
        (r for r in requests if r.get("status") == STATUS_PENDING),
        key=parse_when,
    )
    for req in pending:
        user = requester_name(req)
        cap = quota_for(cfg, user)
        if cap <= 0:  # unlimited
            continue
        cost = request_mb(req, cfg, movies, shows)
        used = usage.get(user, 0.0)
        if used + cost > cap:
            log.info(
                "DECLINE request %s by %s: %.0f MB used + %.0f MB requested > %d MB cap",
                req["id"], user, used, cost, cap,
            )
            try:
                jellyseerr(f"/request/{req['id']}/decline", method="POST")
            except urllib.error.HTTPError as e:
                log.error("Failed to decline request %s: %s", req["id"], e)
        else:
            # Count it against the window so a burst of pendings can't all pass.
            usage[user] = used + cost

    if usage:
        summary = ", ".join(f"{u}={mb:.0f}MB" for u, mb in sorted(usage.items()))
        log.info("Window usage (%d days): %s", cfg["window_days"], summary)


def main() -> None:
    log.info("quota-warden started; enforcing every %ss from %s", INTERVAL, QUOTA_FILE)
    while True:
        try:
            enforce_once()
        except Exception as e:  # noqa: BLE001 - a bad cycle must not kill the loop
            log.error("enforcement cycle failed: %s", e)
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
