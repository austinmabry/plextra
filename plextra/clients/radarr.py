"""Radarr v3 client."""

from __future__ import annotations

import logging
from typing import Any

from .arr import ArrClient, ArrError

log = logging.getLogger(__name__)

# Radarr answers a metadata miss on its lookup endpoints with a 500 rather than
# a 404, and that 500 is deterministic - the title simply is not in its metadata
# server. Retrying three times with backoff just costs six seconds per title and
# fills the log, so lookups get one retry in case the failure really was
# transient, and no more.
_LOOKUP_ATTEMPTS = 2


class RadarrClient(ArrClient):
    service = "Radarr"

    def movies(self) -> list[dict[str, Any]]:
        return self.get_json("api/v3/movie")

    def library_ids(self) -> tuple[set[int], set[str]]:
        """The TMDb *and* IMDb IDs of everything Radarr already holds.

        Matching on TMDb alone is not enough. The same film can sit in Radarr
        under a different TMDb ID than the one a list hands out - duplicate or
        merged TMDb entries are common - and the add then fails late with
        "path is already configured for an existing movie". Checking the IMDb ID
        too catches those up front as "already in library", which is what they
        actually are.
        """
        tmdb_ids: set[int] = set()
        imdb_ids: set[str] = set()
        for movie in self.movies():
            if movie.get("tmdbId"):
                tmdb_ids.add(int(movie["tmdbId"]))
            imdb_id = str(movie.get("imdbId") or "").strip()
            if imdb_id.startswith("tt"):
                imdb_ids.add(imdb_id)
        return tmdb_ids, imdb_ids

    def library_tmdb_ids(self) -> set[int]:
        return self.library_ids()[0]

    def exclusion_tmdb_ids(self) -> set[int]:
        """Radarr renamed this endpoint, so try both spellings."""
        for path in ("api/v3/exclusions", "api/v3/importlistexclusion"):
            try:
                payload = self.get_json(path)
            except ArrError:
                continue
            if isinstance(payload, list):
                return {
                    int(entry["tmdbId"]) for entry in payload if entry.get("tmdbId")
                }
        log.debug("Radarr exposed no exclusion list; continuing without one.")
        return set()

    def resolve_tmdb_id(self, imdb_id: str) -> int | None:
        """Ask Radarr to turn an IMDb ID into the TMDb ID it keys movies by.

        Lists from IMDb, MDBList, StevenLu and plenty of custom feeds identify
        titles by IMDb ID only. Radarr's own search already knows the mapping,
        so use it rather than requiring another API key.
        """
        if not imdb_id:
            return None
        try:
            response = self.request(
                "GET",
                "api/v3/movie/lookup",
                params={"term": f"imdb:{imdb_id}"},
                max_attempts=_LOOKUP_ATTEMPTS,
            )
        except ArrError as exc:
            log.debug("Radarr lookup for IMDb %s failed: %s", imdb_id, exc)
            return None
        if response.status_code != 200:
            return None
        try:
            payload = response.json()
        except ValueError:
            return None
        if isinstance(payload, dict):
            payload = [payload]
        for entry in payload or []:
            if isinstance(entry, dict) and entry.get("tmdbId"):
                return int(entry["tmdbId"])
        return None

    def lookup_tmdb(self, tmdb_id: int) -> dict[str, Any] | None:
        """Ask Radarr to resolve a TMDb ID into a full movie record.

        Building the add payload from Radarr's own lookup is far more reliable
        than hand-assembling one, because it carries titleSlug, images and the
        exact title Radarr expects.
        """
        try:
            response = self.request(
                "GET",
                "api/v3/movie/lookup/tmdb",
                params={"tmdbId": tmdb_id},
                max_attempts=_LOOKUP_ATTEMPTS,
            )
        except ArrError as exc:
            log.debug("Radarr lookup for TMDb %s failed: %s", tmdb_id, exc)
            return None
        if response.status_code != 200:
            log.debug(
                "Radarr lookup for TMDb %s returned %s.", tmdb_id, response.status_code
            )
            return None
        try:
            payload = response.json()
        except ValueError:
            return None

        if isinstance(payload, list):
            payload = next(
                (m for m in payload if str(m.get("tmdbId")) == str(tmdb_id)),
                payload[0] if payload else None,
            )
        if not isinstance(payload, dict) or not payload.get("titleSlug"):
            return None
        return payload

    def resolve_for_add(self, tmdb_id: int, imdb_id: str = "") -> dict[str, Any] | None:
        """The full Radarr record to build an add payload from, or None.

        Tries the TMDb lookup first, then the IMDb one. They go through
        different code paths in Radarr, so a title its metadata server cannot
        serve by TMDb ID sometimes still resolves by IMDb ID.
        """
        lookup = self.lookup_tmdb(tmdb_id)
        if not lookup and imdb_id:
            log.debug("Falling back to an IMDb lookup for %s.", imdb_id)
            resolved = self.resolve_tmdb_id(imdb_id)
            if resolved and resolved != tmdb_id:
                lookup = self.lookup_tmdb(resolved)
        return lookup

    @staticmethod
    def unresolvable(tmdb_id: int, imdb_id: str = "") -> ArrError:
        ident = f"TMDb {tmdb_id}" + (f" / {imdb_id}" if imdb_id else "")
        return ArrError(
            f"Radarr has no metadata for {ident}. Its metadata server does not "
            "know this title, so Radarr's own Add Movie search will not find it "
            "either. This is normal for cancelled, unreleased, fan-made or "
            "TV-special entries that exist on Trakt but not in Radarr."
        )

    def add_movie(
        self,
        tmdb_id: int,
        *,
        quality_profile_id: int,
        root_folder: str,
        minimum_availability: str = "released",
        monitored: bool = True,
        search_on_add: bool = True,
        tags: list[int] | None = None,
        imdb_id: str = "",
    ) -> dict[str, Any]:
        lookup = self.resolve_for_add(tmdb_id, imdb_id)
        if not lookup:
            raise self.unresolvable(tmdb_id, imdb_id)

        payload = dict(lookup)
        payload.update(
            {
                "qualityProfileId": quality_profile_id,
                "rootFolderPath": root_folder,
                "minimumAvailability": minimum_availability,
                "monitored": monitored,
                "tags": sorted(set(tags or [])),
                "addOptions": {
                    "searchForMovie": search_on_add,
                    "monitor": "movieOnly",
                },
            }
        )
        # A lookup result carries id 0; leaving it in makes Radarr treat the
        # POST as an update to a non-existent movie.
        payload.pop("id", None)

        response = self.request("POST", "api/v3/movie", json_body=payload)
        if response.status_code in (200, 201):
            return response.json()
        raise ArrError(self.error_message(response))
