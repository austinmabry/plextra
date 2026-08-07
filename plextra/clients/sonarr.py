"""Sonarr v3/v4 client."""

from __future__ import annotations

import logging
from typing import Any

from .arr import ArrClient, ArrError

log = logging.getLogger(__name__)

# See the note in radarr.py: a metadata miss comes back as a deterministic 500,
# so retrying it three times only wastes time.
_LOOKUP_ATTEMPTS = 2


class SonarrClient(ArrClient):
    service = "Sonarr"

    def series(self) -> list[dict[str, Any]]:
        return self.get_json("api/v3/series")

    def library_ids(self) -> tuple[set[int], set[str]]:
        """The TVDb *and* IMDb IDs of everything Sonarr already holds.

        Matching on TVDb alone misses a series already present under a
        different TVDb ID, which then fails late on a path collision.
        """
        tvdb_ids: set[int] = set()
        imdb_ids: set[str] = set()
        for show in self.series():
            if show.get("tvdbId"):
                tvdb_ids.add(int(show["tvdbId"]))
            imdb_id = str(show.get("imdbId") or "").strip()
            if imdb_id.startswith("tt"):
                imdb_ids.add(imdb_id)
        return tvdb_ids, imdb_ids

    def library_tvdb_ids(self) -> set[int]:
        return self.library_ids()[0]

    def exclusion_tvdb_ids(self) -> set[int]:
        for path in ("api/v3/importlistexclusion", "api/v3/exclusions"):
            try:
                payload = self.get_json(path)
            except ArrError:
                continue
            if isinstance(payload, list):
                return {
                    int(entry["tvdbId"]) for entry in payload if entry.get("tvdbId")
                }
        log.debug("Sonarr exposed no exclusion list; continuing without one.")
        return set()

    def language_profiles(self) -> list[dict[str, Any]]:
        """Sonarr v3 only. v4 removed language profiles entirely."""
        try:
            payload = self.get_json("api/v3/languageprofile")
        except ArrError:
            return []
        return payload if isinstance(payload, list) else []

    def supports_language_profiles(self) -> bool:
        return bool(self.language_profiles())

    def resolve_tvdb_id(self, *, imdb_id: str = "", tmdb_id: int | None = None) -> int | None:
        """Turn an IMDb or TMDb ID into the TVDb ID Sonarr keys series by.

        Most non-Trakt providers hand out IMDb or TMDb IDs, and Sonarr's own
        search already knows the mapping, so no extra API key is needed.
        """
        terms = []
        if imdb_id:
            terms.append(f"imdb:{imdb_id}")
        if tmdb_id:
            terms.append(f"tmdb:{tmdb_id}")

        for term in terms:
            try:
                response = self.request(
                    "GET",
                    "api/v3/series/lookup",
                    params={"term": term},
                    max_attempts=_LOOKUP_ATTEMPTS,
                )
            except ArrError as exc:
                log.debug("Sonarr lookup for %s failed: %s", term, exc)
                continue
            if response.status_code != 200:
                continue
            try:
                payload = response.json()
            except ValueError:
                continue
            if isinstance(payload, dict):
                payload = [payload]
            for entry in payload or []:
                if isinstance(entry, dict) and entry.get("tvdbId"):
                    return int(entry["tvdbId"])
        return None

    @staticmethod
    def unresolvable(tvdb_id: int, imdb_id: str = "") -> ArrError:
        ident = f"TVDb {tvdb_id}" + (f" / {imdb_id}" if imdb_id else "")
        return ArrError(
            f"Sonarr has no metadata for {ident}. Its metadata server does not "
            "know this series, so Sonarr's own Add Series search will not find "
            "it either."
        )

    def lookup_tvdb(self, tvdb_id: int) -> dict[str, Any] | None:
        try:
            response = self.request(
                "GET",
                "api/v3/series/lookup",
                params={"term": f"tvdb:{tvdb_id}"},
                max_attempts=_LOOKUP_ATTEMPTS,
            )
        except ArrError as exc:
            log.debug("Sonarr lookup for TVDb %s failed: %s", tvdb_id, exc)
            return None
        if response.status_code != 200:
            log.debug(
                "Sonarr lookup for TVDb %s returned %s.", tvdb_id, response.status_code
            )
            return None
        try:
            payload = response.json()
        except ValueError:
            return None

        if isinstance(payload, dict):
            payload = [payload]
        if not isinstance(payload, list) or not payload:
            return None

        match = next(
            (s for s in payload if str(s.get("tvdbId")) == str(tvdb_id)), payload[0]
        )
        if not isinstance(match, dict) or not match.get("titleSlug"):
            return None
        return match

    def add_series(
        self,
        tvdb_id: int,
        *,
        quality_profile_id: int,
        root_folder: str,
        monitor: str = "all",
        monitored: bool = True,
        season_folder: bool = True,
        series_type: str = "standard",
        search_on_add: bool = False,
        tags: list[int] | None = None,
        language_profile_id: int | None = None,
    ) -> dict[str, Any]:
        lookup = self.lookup_tvdb(tvdb_id)
        if not lookup:
            raise self.unresolvable(tvdb_id)

        payload = dict(lookup)
        payload.update(
            {
                "qualityProfileId": quality_profile_id,
                "rootFolderPath": root_folder,
                "monitored": monitored,
                "seasonFolder": season_folder,
                "seriesType": series_type,
                "tags": sorted(set(tags or [])),
                "addOptions": {
                    "monitor": monitor,
                    "searchForMissingEpisodes": search_on_add,
                    "searchForCutoffUnmetEpisodes": False,
                },
            }
        )
        if language_profile_id is not None:
            payload["languageProfileId"] = language_profile_id
        payload.pop("id", None)

        response = self.request("POST", "api/v3/series", json_body=payload)
        if response.status_code in (200, 201):
            return response.json()
        raise ArrError(self.error_message(response))
