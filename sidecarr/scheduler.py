"""APScheduler wiring: turns each list's schedule into a background job."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from .config import ConfigStore, ListJob
from .db import Database
from .sync import SyncAlreadyRunning, SyncEngine

log = logging.getLogger("sidecarr.scheduler")

# Never schedule tighter than this, whatever the config says.
MIN_INTERVAL_HOURS = 0.25


class SyncScheduler:
    def __init__(self, store: ConfigStore, engine: SyncEngine, database: Database) -> None:
        self.store = store
        self.engine = engine
        self.db = database
        try:
            self._scheduler = BackgroundScheduler()
        except Exception:  # pragma: no cover - tzlocal edge cases
            log.warning("Falling back to UTC for scheduling; set TZ to change this.")
            self._scheduler = BackgroundScheduler(timezone="UTC")

    # -- lifecycle ----------------------------------------------------------- #

    def start(self) -> None:
        if not self._scheduler.running:
            self._scheduler.start()
        self.reload()

    def shutdown(self) -> None:
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)

    # -- job management ------------------------------------------------------ #

    def reload(self) -> None:
        """Rebuild every job from the current config."""
        self._scheduler.remove_all_jobs()

        scheduled = 0
        for job in self.store.config.lists:
            if not job.enabled or job.schedule.type == "manual":
                continue
            trigger = self._build_trigger(job)
            if trigger is None:
                continue
            self._scheduler.add_job(
                self._run_job,
                trigger=trigger,
                args=[job.id],
                id=job.id,
                name=job.name,
                max_instances=1,
                coalesce=True,
                # A container that was asleep should still catch up once.
                misfire_grace_time=3600,
                replace_existing=True,
            )
            scheduled += 1

        log.info("Scheduler holds %d job%s.", scheduled, "" if scheduled == 1 else "s")

    def _build_trigger(self, job: ListJob) -> IntervalTrigger | CronTrigger | None:
        if job.schedule.type == "cron":
            try:
                return CronTrigger.from_crontab(job.schedule.cron)
            except ValueError as exc:
                log.error(
                    "List %r has an invalid cron expression %r (%s); it will not run "
                    "on a schedule.",
                    job.name,
                    job.schedule.cron,
                    exc,
                )
                return None

        hours = max(MIN_INTERVAL_HOURS, float(job.schedule.hours or MIN_INTERVAL_HOURS))
        interval = timedelta(hours=hours)
        now = datetime.now()

        # Resume the cadence from the last successful run rather than restarting
        # the clock, so a container restart doesn't push the next sync out a day.
        last_success = self.db.last_success_at(job.id)
        if last_success:
            next_run = datetime.fromtimestamp(last_success) + interval
        else:
            next_run = now + timedelta(minutes=1)

        if next_run <= now + timedelta(seconds=30):
            next_run = now + timedelta(minutes=1)

        return IntervalTrigger(seconds=interval.total_seconds(), start_date=next_run)

    # -- execution ----------------------------------------------------------- #

    def _run_job(self, list_id: str) -> None:
        job = self.store.config.find_list(list_id)
        name = job.name if job else list_id

        if self.store.config.scheduler.paused:
            log.info("Scheduler is paused; skipping %r.", name)
            return

        # Skip rather than record a failure when the target is simply down.
        problem = self.engine.preflight(list_id)
        if problem:
            log.warning("Skipping the scheduled run of %r: %s", name, problem)
            return

        try:
            self.engine.run(list_id)
        except SyncAlreadyRunning:
            log.warning("Skipping the scheduled run of %r; it is still running.", name)
        except Exception:  # pragma: no cover - engine already logs specifics
            log.exception("Scheduled run of %r failed.", name)

    # -- introspection -------------------------------------------------------- #

    def next_runs(self) -> dict[str, str | None]:
        result: dict[str, str | None] = {}
        for job in self._scheduler.get_jobs():
            result[job.id] = job.next_run_time.isoformat() if job.next_run_time else None
        return result
