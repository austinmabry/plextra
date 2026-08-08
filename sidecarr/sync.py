"""The sync engine: a list from any provider in, Radarr/Sonarr additions out."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from . import filters as filter_rules
from . import providers as provider_registry
from . import settings
from .clients import ArrError, ArrMetadataError, RadarrClient, SonarrClient
from .config import AppConfig, ConfigStore, ListJob, TraktAccount
from .db import Database
from .providers.base import MediaItem, Provider, ProviderError

log = logging.getLogger("sidecarr.sync")


class SyncAlreadyRunning(Exception):
    """A sync for this list is already in flight."""


class SyncConfigError(Exception):
    """The list or its target is not configured well enough to run."""


@dataclass
class SyncResult:
    run_id: int
    list_id: str
    list_name: str
    status: str = "success"
    dry_run: bool = False
    fetched: int = 0
    candidates: int = 0
    filtered: int = 0
    existing: int = 0
    excluded: int = 0
    unresolved: int = 0
    added: int = 0
    failed: int = 0
    message: str = ""
    added_titles: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "list_id": self.list_id,
            "list_name": self.list_name,
            "status": self.status,
            "dry_run": self.dry_run,
            "fetched": self.fetched,
            "candidates": self.candidates,
            "filtered": self.filtered,
            "existing": self.existing,
            "excluded": self.excluded,
            "unresolved": self.unresolved,
            "added": self.added,
            "failed": self.failed,
            "message": self.message,
            "added_titles": self.added_titles,
        }


class SyncEngine:
    def __init__(self, store: ConfigStore, database: Database) -> None:
        self.store = store
        self.db = database
        self._guard = threading.Lock()
        self._running: set[str] = set()

    # -- state -------------------------------------------------------------- #

    def is_running(self, list_id: str) -> bool:
        with self._guard:
            return list_id in self._running

    def running_lists(self) -> set[str]:
        with self._guard:
            return set(self._running)

    def _acquire(self, list_id: str) -> None:
        with self._guard:
            if list_id in self._running:
                raise SyncAlreadyRunning(f"A sync for {list_id} is already running.")
            self._running.add(list_id)

    def _release(self, list_id: str) -> None:
        with self._guard:
            self._running.discard(list_id)

    def preflight(self, list_id: str) -> str | None:
        """Check the target answers before a scheduled run starts.

        A Radarr that is down for maintenance would otherwise produce a failed
        run every few hours, burying the failures that mean something.
        """
        config = self.store.config
        job = config.find_list(list_id)
        if job is None:
            return f"No list with id {list_id!r}."

        target = config.radarr if job.media_type == "movie" else config.sonarr
        name = "Radarr" if job.media_type == "movie" else "Sonarr"
        if not target.configured:
            return f"{name} is not enabled or not configured."

        try:
            with self._build_client(target, job.media_type) as arr:
                arr.system_status()
        except ArrError as exc:
            return f"{name} is not responding: {exc}"
        return None

    # -- entry point --------------------------------------------------------- #

    def run(self, list_id: str, dry_run: bool | None = None) -> SyncResult:
        config = self.store.config
        job = config.find_list(list_id)
        if job is None:
            raise SyncConfigError(f"No list with id {list_id!r}.")

        effective_dry_run = job.dry_run if dry_run is None else dry_run
        self._acquire(list_id)
        run_id = self.db.start_run(job.id, job.name, job.media_type, effective_dry_run)
        result = SyncResult(
            run_id=run_id, list_id=job.id, list_name=job.name, dry_run=effective_dry_run
        )

        try:
            self._execute(config, job, result)
        except (SyncConfigError, ProviderError, ArrError) as exc:
            result.status = "error"
            result.message = str(exc)
            log.error("[%s] %s", job.name, exc)
        except Exception as exc:  # pragma: no cover - defensive
            result.status = "error"
            result.message = f"Unexpected error: {exc}"
            log.exception("[%s] Sync crashed.", job.name)
        finally:
            self._release(list_id)
            self.db.finish_run(
                run_id,
                result.status,
                result.message,
                candidates=result.candidates,
                filtered=result.filtered,
                existing=result.existing,
                excluded=result.excluded,
                added=result.added,
                failed=result.failed,
            )
            self.db.prune()

        return result

    # -- the actual work ----------------------------------------------------- #

    def _execute(self, config: AppConfig, job: ListJob, result: SyncResult) -> None:
        media = job.media_type
        target = config.radarr if media == "movie" else config.sonarr
        target_name = "Radarr" if media == "movie" else "Sonarr"

        if not target.configured:
            raise SyncConfigError(
                f"{target_name} is not enabled or not configured. Set it up in Settings."
            )

        quality_profile_id = job.quality_profile_id or target.quality_profile_id
        root_folder = job.root_folder or target.root_folder
        if not quality_profile_id:
            raise SyncConfigError(f"No {target_name} quality profile chosen.")
        if not root_folder:
            raise SyncConfigError(f"No {target_name} root folder chosen.")

        search_on_add = (
            target.search_on_add if job.search_on_add is None else job.search_on_add
        )
        tags = sorted(set(target.tags) | set(job.tags))

        with self._build_provider(config, job) as provider:
            if not provider.configured():
                raise SyncConfigError(
                    f"{provider.name} is not set up yet. {provider.setup_hint}".strip()
                )

            log.info(
                "[%s] Starting %s sync from %s %s%s.",
                job.name,
                media,
                provider.name,
                job.source.type,
                " (dry run)" if result.dry_run else "",
            )
            items = provider.fetch(job.source, media, max_items=job.limit)

        items = provider_registry.dedupe(items)
        result.fetched = len(items)
        log.info(
            "[%s] %s returned %d %s.",
            job.name,
            provider.name,
            len(items),
            _plural(media, len(items)),
        )

        with self._build_client(target, media) as arr:
            library, library_imdb = arr.library_ids()
            exclusions = (
                arr.exclusion_tmdb_ids() if media == "movie" else arr.exclusion_tvdb_ids()
            )
            log.info(
                "[%s] %s already holds %d titles and excludes %d.",
                job.name,
                target_name,
                len(library),
                len(exclusions),
            )

            language_profile_id = None
            if media == "show" and config.sonarr.language_profile_id:
                # v4 dropped language profiles; only send one if the server has them.
                if arr.supports_language_profiles():
                    language_profile_id = config.sonarr.language_profile_id

            candidates = self._select(
                job, provider, arr, items, library, library_imdb, exclusions, result
            )
            result.candidates = len(candidates)

            if not candidates:
                result.message = "Nothing new to add."
                log.info("[%s] Nothing new to add.", job.name)
                return

            self._add_all(
                job=job,
                arr=arr,
                media=media,
                candidates=candidates,
                result=result,
                quality_profile_id=quality_profile_id,
                root_folder=root_folder,
                search_on_add=search_on_add,
                tags=tags,
                language_profile_id=language_profile_id,
                target=target,
            )

        if result.failed and result.added:
            result.status = "partial"
        elif result.failed and not result.added:
            result.status = "error"
            result.message = result.message or f"All {result.failed} additions failed."

        if not result.message:
            verb = "Would add" if result.dry_run else "Added"
            result.message = f"{verb} {result.added} of {result.candidates} eligible titles."
        log.info("[%s] %s", job.name, result.message)

    # -- steps --------------------------------------------------------------- #

    def _build_provider(self, config: AppConfig, job: ListJob) -> Provider:
        def persist(username: str, account: TraktAccount) -> None:
            self.store.mutate(lambda cfg: cfg.trakt.accounts.__setitem__(username, account))

        return provider_registry.build(
            job.source.provider, config, on_account_update=persist
        )

    @staticmethod
    def _build_client(target: Any, media: str) -> Any:
        if media == "movie":
            return RadarrClient(target.url, target.api_key)
        return SonarrClient(target.url, target.api_key)

    def _select(
        self,
        job: ListJob,
        provider: Provider,
        arr: Any,
        items: list[MediaItem],
        library: set[int],
        library_imdb: set[str],
        exclusions: set[int],
        result: SyncResult,
    ) -> list[tuple[MediaItem, int]]:
        """Filter, sort, then walk the list resolving IDs until the limit is met.

        ID resolution can cost an HTTP request per item, so it happens here -
        lazily, in priority order, stopping as soon as enough eligible titles
        are found - rather than up front for the whole list.
        """
        media = job.media_type
        id_label = "TMDb" if media == "movie" else "TVDb"
        skipped: list[tuple[str, int | None, str | None, str, str]] = []

        passed: list[MediaItem] = []
        for item in items:
            reason = filter_rules.evaluate(item, media, job.filters, provider.name)
            if reason:
                result.filtered += 1
                skipped.append((item.label, item.year, None, "filtered", reason))
                log.debug("[%s] %s filtered: %s", job.name, item.label, reason)
            else:
                passed.append(item)

        passed = filter_rules.sort_items(passed, media, job.sort)

        candidates: list[tuple[MediaItem, int]] = []
        for item in passed:
            if job.limit and len(candidates) >= job.limit:
                break

            ident = item.target_id(media)
            if ident is None:
                ident = self._resolve(item, media, provider, arr)
            if ident is None:
                result.unresolved += 1
                skipped.append(
                    (item.label, item.year, item.imdb_id, "skipped", f"no {id_label} ID found")
                )
                log.debug("[%s] Could not resolve a %s ID for %s.", job.name, id_label, item.label)
                continue

            if ident in library:
                result.existing += 1
                skipped.append((item.label, item.year, str(ident), "existing", "already in library"))
                continue
            if item.imdb_id and item.imdb_id in library_imdb:
                # Same film, different TMDb ID. Catching it here turns what
                # would be a late "path is already configured" failure into an
                # accurate "you already have this".
                result.existing += 1
                skipped.append((
                    item.label, item.year, item.imdb_id, "existing",
                    "already in library, under a different ID",
                ))
                continue
            if ident in exclusions:
                result.excluded += 1
                skipped.append((item.label, item.year, str(ident), "excluded", "on the exclusion list"))
                continue

            candidates.append((item, ident))

        self.db.add_items(result.run_id, skipped)
        log.info(
            "[%s] %d eligible, %d already present, %d excluded, %d filtered out, %d unresolved.",
            job.name,
            len(candidates),
            result.existing,
            result.excluded,
            result.filtered,
            result.unresolved,
        )
        return candidates

    @staticmethod
    def _resolve(item: MediaItem, media: str, provider: Provider, arr: Any) -> int | None:
        """Find the ID the target needs, by whatever route is available."""
        # 1. The provider's own API, e.g. TMDb's external IDs for a show.
        try:
            provider.resolve_ids(item, media)
        except ProviderError as exc:
            log.debug("Provider could not resolve IDs for %s: %s", item.label, exc)
        ident = item.target_id(media)
        if ident is not None:
            return ident

        # 2. Radarr/Sonarr's own search, which already knows the cross-mappings.
        try:
            if media == "movie":
                if item.imdb_id:
                    ident = arr.resolve_tmdb_id(item.imdb_id)
            else:
                ident = arr.resolve_tvdb_id(
                    imdb_id=item.imdb_id or "", tmdb_id=item.numeric_id("tmdb")
                )
            if ident is not None:
                return ident
        except ArrError as exc:
            log.debug("Lookup failed for %s: %s", item.label, exc)

        # 3. The title, for sources that carry no ID at all - a Letterboxd list
        #    or a CSV export gives only a name and a year. Strict matching lives
        #    in the client, so a near miss stays unresolved.
        if item.title:
            try:
                return arr.resolve_by_title(item.title, item.year)
            except ArrError as exc:
                log.debug("Title search failed for %s: %s", item.label, exc)
        return None

    def _add_all(
        self,
        *,
        job: ListJob,
        arr: Any,
        media: str,
        candidates: list[tuple[MediaItem, int]],
        result: SyncResult,
        quality_profile_id: int,
        root_folder: str,
        search_on_add: bool,
        tags: list[int],
        language_profile_id: int | None,
        target: Any,
    ) -> None:
        settings_kwargs: dict[str, Any] = (
            {
                "quality_profile_id": quality_profile_id,
                "root_folder": root_folder,
                "minimum_availability": target.minimum_availability,
                "monitored": target.monitored,
                "search_on_add": search_on_add,
                "tags": tags,
            }
            if media == "movie"
            else {
                "quality_profile_id": quality_profile_id,
                "root_folder": root_folder,
                "monitor": target.monitor,
                "monitored": target.monitored,
                "season_folder": target.season_folder,
                "series_type": target.series_type,
                "search_on_add": search_on_add,
                "tags": tags,
                "language_profile_id": language_profile_id,
            }
        )

        if result.dry_run:
            for item, ident in candidates:
                self._dry_run_one(job, arr, media, item, ident, result)
            return

        # Build a batch, then hand the whole thing to Radarr/Sonarr at once.
        # One request per 50 titles rather than 50 requests lets the target
        # resolve them against its metadata service in bulk, which is what
        # keeps a large first sync from tripping rate limits.
        batch: list[dict[str, Any]] = []
        batch_meta: list[tuple[MediaItem, int]] = []

        for item, ident in candidates:
            record = self._record_for(arr, media, item, ident, job, result)
            if record is None:
                continue

            if media == "movie":
                batch.append(arr.movie_payload(record, **settings_kwargs))
            else:
                batch.append(arr.series_payload(record, **settings_kwargs))
            batch_meta.append((item, ident))

            if len(batch) >= settings.BULK_BATCH_SIZE:
                self._flush_batch(job, arr, media, batch, batch_meta, result, settings_kwargs)
                batch, batch_meta = [], []
                if settings.ADD_DELAY_SECONDS > 0:
                    time.sleep(settings.ADD_DELAY_SECONDS)

        self._flush_batch(job, arr, media, batch, batch_meta, result, settings_kwargs)

    def _record_for(
        self,
        arr: Any,
        media: str,
        item: MediaItem,
        ident: int,
        job: ListJob,
        result: SyncResult,
    ) -> dict[str, Any] | None:
        """Resolve the full target record a payload is built from."""
        try:
            record = (
                arr.resolve_for_add(ident, item.imdb_id or "")
                if media == "movie"
                else arr.lookup_tvdb(ident)
            )
        except ArrError as exc:
            self._record_failure(job, item, ident, result, exc)
            return None
        if not record:
            self._record_failure(job, item, ident, result, arr.unknown_id(ident, item.imdb_id or ""))
            return None
        return record

    def _flush_batch(
        self,
        job: ListJob,
        arr: Any,
        media: str,
        batch: list[dict[str, Any]],
        batch_meta: list[tuple[MediaItem, int]],
        result: SyncResult,
        settings_kwargs: dict[str, Any],
    ) -> None:
        if not batch:
            return

        id_field = "tmdbId" if media == "movie" else "tvdbId"
        try:
            added = (
                arr.bulk_add_movies(batch) if media == "movie" else arr.bulk_add_series(batch)
            )
        except ArrError as exc:
            # The whole batch was rejected. Fall back to adding them one at a
            # time so each title gets a real reason rather than sharing one.
            log.warning(
                "[%s] Bulk add of %d titles failed (%s); retrying individually.",
                job.name,
                len(batch),
                exc,
            )
            for item, ident in batch_meta:
                self._add_one(job, arr, media, item, ident, result, settings_kwargs)
            return

        accepted = {entry.get(id_field) for entry in added if entry.get(id_field)}
        for item, ident in batch_meta:
            if ident in accepted:
                result.added += 1
                result.added_titles.append(item.label)
                self.db.add_item(result.run_id, item.label, item.year, str(ident), "added", "")
                log.info("[%s] Added %s.", job.name, item.label)
            else:
                # Accepted by the batch call but absent from the reply. Retry it
                # alone to find out why instead of guessing.
                log.debug(
                    "[%s] %s was not in the bulk reply; retrying alone.", job.name, item.label
                )
                self._add_one(job, arr, media, item, ident, result, settings_kwargs)

    def _add_one(
        self,
        job: ListJob,
        arr: Any,
        media: str,
        item: MediaItem,
        ident: int,
        result: SyncResult,
        settings_kwargs: dict[str, Any],
    ) -> None:
        try:
            if media == "movie":
                arr.add_movie(ident, imdb_id=item.imdb_id or "", **settings_kwargs)
            else:
                arr.add_series(ident, **settings_kwargs)
        except ArrError as exc:
            self._record_failure(job, item, ident, result, exc)
        else:
            result.added += 1
            result.added_titles.append(item.label)
            self.db.add_item(result.run_id, item.label, item.year, str(ident), "added", "")
            log.info("[%s] Added %s.", job.name, item.label)

    def _record_failure(
        self, job: ListJob, item: MediaItem, ident: int, result: SyncResult, exc: Exception
    ) -> None:
        result.failed += 1
        self.db.add_item(result.run_id, item.label, item.year, str(ident), "failed", str(exc))
        if isinstance(exc, ArrMetadataError):
            log.error(
                "[%s] Could not add %s: %s The next sync will retry it.",
                job.name,
                item.label,
                exc,
            )
        else:
            log.error("[%s] Could not add %s: %s", job.name, item.label, exc)

    def _dry_run_one(
        self,
        job: ListJob,
        arr: Any,
        media: str,
        item: MediaItem,
        ident: int,
        result: SyncResult,
    ) -> None:
        """Resolve without writing, so a dry run cannot promise a failing add."""
        failure: Exception | None = None
        try:
            resolved = (
                arr.resolve_for_add(ident, item.imdb_id or "")
                if media == "movie"
                else arr.lookup_tvdb(ident)
            )
            if not resolved:
                failure = arr.unknown_id(ident, item.imdb_id or "")
        except ArrError as exc:
            failure = exc

        if failure is None:
            result.added += 1
            result.added_titles.append(item.label)
            self.db.add_item(
                result.run_id, item.label, item.year, str(ident), "dry_run", "would be added"
            )
            log.info("[%s] Would add %s.", job.name, item.label)
        else:
            result.failed += 1
            self.db.add_item(
                result.run_id, item.label, item.year, str(ident), "failed", str(failure)
            )
            log.error("[%s] Would fail on %s: %s", job.name, item.label, failure)


def _plural(media: str, count: int) -> str:
    word = "movie" if media == "movie" else "show"
    return word if count == 1 else f"{word}s"
