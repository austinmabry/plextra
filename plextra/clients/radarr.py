"""Radarr v3 client."""

from __future__ import annotations

import logging
from typing import Any

from .arr import ArrClient, ArrError

log = logging.getLogger(__name__)


class RadarrClient(ArrClient):
    service = "Radarr"

    def movies(self) -> list[dict[str, Any]]:
        return self.get_json("api/v3/movie")

    def library_tmdb_ids(self) -> set[int]:
        return {
            int(movie["tmdbId"])
            for movie in self.movies()
            if movie.get("tmdbId")
        }

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
        response = self.request("GET", "api/v3/movie/lookup", params={"term": f"imdb:{imdb_id}"})
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
        response = self.request(
            "GET", "api/v3/movie/lookup/tmdb", params={"tmdbId": tmdb_id}
        )
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
    ) -> dict[str, Any]:
        lookup = self.lookup_tmdb(tmdb_id)
        if not lookup:
            raise ArrError(f"Radarr could not resolve TMDb ID {tmdb_id}.")

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
