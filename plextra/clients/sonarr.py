"""Sonarr v3/v4 client."""

from __future__ import annotations

import logging
from typing import Any

from .arr import ArrClient, ArrError

log = logging.getLogger(__name__)


class SonarrClient(ArrClient):
    service = "Sonarr"

    def series(self) -> list[dict[str, Any]]:
        return self.get_json("api/v3/series")

    def library_tvdb_ids(self) -> set[int]:
        return {
            int(show["tvdbId"])
            for show in self.series()
            if show.get("tvdbId")
        }

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

    def lookup_tvdb(self, tvdb_id: int) -> dict[str, Any] | None:
        response = self.request(
            "GET", "api/v3/series/lookup", params={"term": f"tvdb:{tvdb_id}"}
        )
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
            raise ArrError(f"Sonarr could not resolve TVDb ID {tvdb_id}.")

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
