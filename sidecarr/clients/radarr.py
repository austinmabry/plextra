"""Radarr v3 client."""

from __future__ import annotations

import logging
from typing import Any

from .arr import ArrClient, ArrError, ArrMetadataError, ArrUnknownIdError

log = logging.getLogger(__name__)


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


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
                "GET", "api/v3/movie/lookup", params={"term": f"imdb:{imdb_id}"}
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

    def resolve_by_title(self, title: str, year: int | None = None) -> int | None:
        """Last resort: find a TMDb ID from a title, the way the Add Movie box does.

        Some sources carry no ID at all - a Letterboxd list or CSV export gives
        only a name and a year. Radarr's search is the same one a human would
        use, but a search result is a guess in a way an ID never is, so this is
        deliberately strict: with a year, only an exact year match counts; with
        no year, only an exact title match. Anything less and the item is left
        unresolved rather than risking the wrong film in someone's library.
        """
        title = (title or "").strip()
        if not title:
            return None
        try:
            response = self.request("GET", "api/v3/movie/lookup", params={"term": title})
        except ArrError as exc:
            log.debug("Radarr title search for %r failed: %s", title, exc)
            return None
        if response.status_code != 200:
            return None
        try:
            payload = response.json()
        except ValueError:
            return None
        if isinstance(payload, dict):
            payload = [payload]

        wanted = title.casefold()
        for entry in payload or []:
            if not isinstance(entry, dict) or not entry.get("tmdbId"):
                continue
            if year:
                if _int_or_none(entry.get("year")) == year:
                    return int(entry["tmdbId"])
                continue
            names = {
                str(entry.get(key, "")).strip().casefold()
                for key in ("title", "originalTitle", "sortTitle")
            }
            if wanted in names:
                return int(entry["tmdbId"])

        log.debug("Radarr had no confident match for %r (%s).", title, year or "no year")
        return None

    def lookup_tmdb(self, tmdb_id: int) -> dict[str, Any] | None:
        """Ask Radarr to resolve a TMDb ID into a full movie record.

        Building the add payload from Radarr's own lookup is far more reliable
        than hand-assembling one, because it carries titleSlug, images and the
        exact title Radarr expects.
        """
        # A 5xx here propagates as ArrMetadataError rather than being swallowed:
        # it means Radarr's metadata service failed, which is worth retrying,
        # and is a different thing from the title being unknown.
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

    def resolve_for_add(self, tmdb_id: int, imdb_id: str = "") -> dict[str, Any] | None:
        """The full Radarr record to build an add payload from, or None.

        Tries the TMDb lookup first, then the IMDb one. They go through
        different code paths in Radarr - ``lookup/tmdb`` fetches metadata for one
        ID, while the IMDb route runs a search - so a title the first cannot
        serve sometimes still resolves through the second.

        A 5xx from the TMDb lookup must not skip that fallback. Radarr answers a
        genuine miss with a clean 404 and turns *any other* metadata failure into
        a 500, so a 500 is exactly the case where the other route is worth trying.
        """
        metadata_error: ArrMetadataError | None = None
        try:
            lookup = self.lookup_tmdb(tmdb_id)
        except ArrMetadataError as exc:
            metadata_error = exc
            lookup = None

        if not lookup and imdb_id:
            log.debug("Falling back to an IMDb search for %s.", imdb_id)
            try:
                # Use the record the search itself returns. Taking only its TMDb
                # ID and looking that up again would just repeat the request that
                # already failed.
                lookup = self.lookup_imdb(imdb_id)
            except ArrMetadataError as exc:
                metadata_error = metadata_error or exc

        if lookup:
            return lookup
        if metadata_error is not None:
            raise metadata_error
        return None

    def lookup_imdb(self, imdb_id: str) -> dict[str, Any] | None:
        """The full record from Radarr's IMDb search, ready to build a payload.

        ``movie/lookup?term=imdb:…`` runs a search, which is a different route
        through Radarr than the per-ID metadata fetch ``lookup/tmdb`` uses.
        """
        if not imdb_id:
            return None
        response = self.request(
            "GET", "api/v3/movie/lookup", params={"term": f"imdb:{imdb_id}"}
        )
        if response.status_code != 200:
            return None
        try:
            payload = response.json()
        except ValueError:
            return None
        if isinstance(payload, dict):
            payload = [payload]
        for entry in payload or []:
            if isinstance(entry, dict) and entry.get("titleSlug") and entry.get("tmdbId"):
                return entry
        return None

    @staticmethod
    def unknown_id(tmdb_id: int, imdb_id: str = "") -> ArrUnknownIdError:
        """Radarr's metadata service returned a clean 404 for this ID."""
        ident = f"TMDb {tmdb_id}" + (f" / {imdb_id}" if imdb_id else "")
        return ArrUnknownIdError(
            f"Radarr does not recognise {ident}. Its metadata service returned "
            "a clean 'not found', so Radarr's own Add Movie search will not "
            "find it either - usually a TMDb entry that was deleted or merged "
            "after the list was built."
        )

    @staticmethod
    def movie_payload(
        record: dict[str, Any],
        *,
        quality_profile_id: int,
        root_folder: str,
        minimum_availability: str = "released",
        monitored: bool = True,
        search_on_add: bool = True,
        tags: list[int] | None = None,
    ) -> dict[str, Any]:
        """Turn a Radarr lookup record into an add payload."""
        payload = dict(record)
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
        return payload

    def bulk_add_movies(self, payloads: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Add many movies in one request via Radarr's bulk import endpoint.

        Radarr handles a batch far better than the same titles one at a time:
        it resolves the batch against its metadata service in one go rather than
        once per title, which is what makes a large first sync trip rate limits.

        Returns the records Radarr accepted. Anything absent from that list was
        rejected, and the caller retries it individually to learn why.
        """
        if not payloads:
            return []
        response = self.request(
            "POST",
            "api/v3/movie/import",
            json_body=payloads,
            # A batch of 50 is a lot of work for Radarr; give it room.
            timeout=max(120.0, 6.0 * len(payloads)),
        )
        if response.status_code not in (200, 201, 202):
            raise ArrError(self.error_message(response))
        try:
            added = response.json()
        except ValueError as exc:
            raise ArrError("Radarr sent a non-JSON reply to the bulk import.") from exc
        return [entry for entry in added if isinstance(entry, dict)]

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
            raise self.unknown_id(tmdb_id, imdb_id)

        payload = self.movie_payload(
            lookup,
            quality_profile_id=quality_profile_id,
            root_folder=root_folder,
            minimum_availability=minimum_availability,
            monitored=monitored,
            search_on_add=search_on_add,
            tags=tags,
        )
        response = self.request("POST", "api/v3/movie", json_body=payload)
        if response.status_code in (200, 201):
            return response.json()
        raise ArrError(self.error_message(response))
