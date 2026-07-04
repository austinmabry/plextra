#!/usr/bin/env python3
"""Aurora Home Rows — curated, self-updating rows for EVERY Jellyfin client.

CSS can only restyle web-based clients; native apps (iOS, Roku, Android TV,
Swiftfin) draw their own UI. The one surface they ALL render is the library
itself — so this engine expresses "home rows" as smart COLLECTIONS, rebuilt
on a schedule from rules you define in rows.yml:

    Recently Released    ·  New This Week   ·  Top Rated: Action
    Hidden Gems          ·  90s Movies      ·  Short & Sweet  ·  ...

Every client shows collections; the web/TV web clients additionally render
them as Netflix-style rows under the Aurora theme. Pin favorites: web users
can add sections in Display settings; native apps list them under
Collections/My Media.

Config: rows.yml     Auth: JELLYFIN_URL + JELLYFIN_API_KEY (admin key)
Runs once with RUN_ONCE=1, otherwise loops every INTERVAL_SECONDS (6h default).
"""

import json
import logging
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

import yaml

log = logging.getLogger("homerows")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

BASE = os.environ["JELLYFIN_URL"].rstrip("/")
KEY = os.environ["JELLYFIN_API_KEY"]
CONFIG = os.environ.get("ROWS_FILE", "rows.yml")
INTERVAL = int(os.environ.get("INTERVAL_SECONDS", "21600"))
PREFIX = os.environ.get("ROW_PREFIX", "")  # e.g. "· " to group/sort rows


def api(path: str, method: str = "GET", params: dict | None = None,
        timeout: int = 60):
    url = BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, method=method)
    req.add_header("X-Emby-Token", KEY)
    req.add_header("Accept", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read()
    return json.loads(body) if body else None


def probe() -> bool:
    """Fast, unauthenticated reachability check with actionable guidance."""
    try:
        api("/System/Info/Public", timeout=8)
        return True
    except Exception as e:  # noqa: BLE001
        log.error("cannot reach Jellyfin at %s (%s)", BASE, e)
        log.error("checklist:")
        log.error(" 1) JELLYFIN_URL must be reachable FROM INSIDE this container:")
        log.error("    use the host's LAN IP, never localhost/127.0.0.1, and")
        log.error("    never a 172.x Docker IP from another compose project")
        log.error(" 2) Jellyfin in Docker on this host? either join its network")
        log.error("    and use http://<container-name>:8096, or uncomment")
        log.error("    'network_mode: host' in docker-compose.yml")
        log.error(" 3) Unraid br0/macvlan: bridge containers cannot reach br0 IPs")
        log.error("    unless Settings > Docker > 'Host access to custom networks'")
        log.error("    is enabled")
        log.error(" 4) don't use the mesh/VPN hostname here — plain LAN URL only")
        return False


def build_query(rule: dict) -> dict:
    """Translate a rows.yml rule into an /Items query."""
    q = {
        "Recursive": "true",
        "IncludeItemTypes": rule.get("types", "Movie"),
        "Limit": str(rule.get("limit", 25)),
        "Fields": "PremiereDate",
    }
    sort = rule.get("sort", "DateCreated")
    q["SortBy"] = sort
    q["SortOrder"] = rule.get("order", "Descending")
    if rule.get("genres"):
        q["Genres"] = "|".join(rule["genres"]) if isinstance(rule["genres"], list) else rule["genres"]
    if rule.get("unplayed"):
        q["Filters"] = "IsUnplayed"
    if rule.get("min_rating") is not None:
        q["MinCommunityRating"] = str(rule["min_rating"])
    if rule.get("released_within_days"):
        cutoff = datetime.now(timezone.utc) - timedelta(days=int(rule["released_within_days"]))
        q["MinPremiereDate"] = cutoff.strftime("%Y-%m-%dT00:00:00Z")
    if rule.get("added_within_days"):
        cutoff = datetime.now(timezone.utc) - timedelta(days=int(rule["added_within_days"]))
        q["MinDateLastSaved"] = cutoff.strftime("%Y-%m-%dT00:00:00Z")
    if rule.get("years"):
        q["Years"] = ",".join(str(y) for y in rule["years"])
    if rule.get("decade"):
        start = int(rule["decade"])
        q["Years"] = ",".join(str(y) for y in range(start, start + 10))
    if rule.get("max_runtime_minutes"):
        q["MaxRuntimeTicks"] = str(int(rule["max_runtime_minutes"]) * 60 * 10_000_000)
    if rule.get("tags"):
        q["Tags"] = "|".join(rule["tags"]) if isinstance(rule["tags"], list) else rule["tags"]
    if rule.get("parental_ratings"):
        q["OfficialRatings"] = "|".join(rule["parental_ratings"])
    return q


def find_items(rule: dict) -> list[str]:
    data = api("/Items", params=build_query(rule))
    return [item["Id"] for item in data.get("Items", [])]


def existing_collections() -> dict:
    """name -> collection id."""
    data = api("/Items", params={
        "Recursive": "true", "IncludeItemTypes": "BoxSet",
        "Fields": "", "Limit": "500",
    })
    return {item["Name"]: item["Id"] for item in data.get("Items", [])}


def collection_children(cid: str) -> list[str]:
    data = api("/Items", params={"ParentId": cid, "Limit": "1000"})
    return [item["Id"] for item in data.get("Items", [])]


def sync_row(name: str, rule: dict, collections: dict) -> None:
    display = f"{PREFIX}{name}"
    want = find_items(rule)
    if not want:
        log.warning("row %r matched nothing — check its rule; leaving as-is", display)
        return

    cid = collections.get(display)
    if cid is None:
        api("/Collections", method="POST",
            params={"Name": display, "Ids": ",".join(want)})
        log.info("created %r with %d items", display, len(want))
        return

    have = collection_children(cid)
    to_add = [i for i in want if i not in have]
    to_remove = [i for i in have if i not in want]
    if to_add:
        api(f"/Collections/{cid}/Items", method="POST",
            params={"Ids": ",".join(to_add)})
    if to_remove:
        api(f"/Collections/{cid}/Items", method="DELETE",
            params={"Ids": ",".join(to_remove)})
    log.info("synced %r: %d items (+%d / -%d)",
             display, len(want), len(to_add), len(to_remove))


def sync_all() -> None:
    with open(CONFIG) as f:
        cfg = yaml.safe_load(f) or {}
    rows = cfg.get("rows") or {}
    if not rows:
        log.error("no rows defined in %s", CONFIG)
        return
    collections = existing_collections()
    for name, rule in rows.items():
        try:
            sync_row(name, rule or {}, collections)
        except Exception as e:  # noqa: BLE001 - one bad row must not stop the rest
            log.error("row %r failed: %s", name, e)


def main() -> None:
    if os.environ.get("RUN_ONCE"):
        if not probe():
            sys.exit(1)
        sync_all()
        return
    log.info("homerows started; syncing every %ss from %s", INTERVAL, CONFIG)
    while True:
        if not probe():
            time.sleep(60)  # retry connectivity soon, not in 6 hours
            continue
        try:
            sync_all()
        except Exception as e:  # noqa: BLE001
            log.error("sync failed: %s", e)
        time.sleep(INTERVAL)


if __name__ == "__main__":
    sys.exit(main())
