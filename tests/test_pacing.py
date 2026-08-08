"""The add-rate limiter: hold most of a big first sync back, release it slowly.

Adding 990 titles at once is what strains a setup - each one makes the target
search every indexer and hand results to a download client. These check the cap
is respected, that nothing is lost or double-added, and that turning the limit
off does not strand a backlog.
"""

import pytest

from test_sync import add_list, movie, tvshow  # noqa: F401  (fixtures come with it)
from test_sync import *  # noqa: F401,F403  (engine/store/database/radarr fixtures)


@pytest.fixture
def paced(store):
    """Ten titles per ten minutes, the shape the feature was asked for."""

    def apply(max_adds=10, window_minutes=10, enabled=True):
        store.mutate(lambda c: setattr(c.pacing, "enabled", enabled))
        store.mutate(lambda c: setattr(c.pacing, "max_adds", max_adds))
        store.mutate(lambda c: setattr(c.pacing, "window_minutes", window_minutes))

    return apply


def big_list(count, start=1):
    return [movie(i, f"Film {i}") for i in range(start, start + count)]


class TestHoldingBack:
    def test_only_the_allowance_is_added(self, engine, store, source_items, radarr, paced):
        paced(max_adds=10)
        source_items["items"] = big_list(50)
        job = add_list(store, name="Big")

        result = engine.run(job.id)

        assert result.added == 10
        assert len(radarr["added"]) == 10

    def test_the_rest_is_queued_not_dropped(self, engine, store, source_items, radarr, paced, database):
        paced(max_adds=10)
        source_items["items"] = big_list(50)
        job = add_list(store, name="Big")

        result = engine.run(job.id)

        assert result.queued == 40
        assert database.queue_counts()[job.id] == 40

    def test_nothing_is_lost(self, engine, store, source_items, radarr, paced, database):
        paced(max_adds=10)
        source_items["items"] = big_list(50)
        job = add_list(store, name="Big")

        result = engine.run(job.id)

        assert result.added + result.queued == 50

    def test_a_list_inside_the_allowance_is_untouched(self, engine, store, source_items, radarr, paced):
        paced(max_adds=10)
        source_items["items"] = big_list(3)
        job = add_list(store, name="Small")

        result = engine.run(job.id)

        assert (result.added, result.queued) == (3, 0)

    def test_the_window_is_shared_across_lists(self, engine, store, source_items, radarr, paced):
        """The download client is one resource, so the limit is global."""
        paced(max_adds=10)
        source_items["items"] = big_list(8)
        first = add_list(store, name="One")
        engine.run(first.id)

        source_items["items"] = big_list(8, start=100)
        second = add_list(store, name="Two")
        result = engine.run(second.id)

        assert result.added == 2
        assert result.queued == 6

    def test_a_full_window_adds_nothing_now(self, engine, store, source_items, radarr, paced):
        paced(max_adds=5)
        source_items["items"] = big_list(5)
        engine.run(add_list(store, name="One").id)

        source_items["items"] = big_list(5, start=100)
        result = engine.run(add_list(store, name="Two").id)

        assert (result.added, result.queued) == (0, 5)
        assert "Rate limit reached" in result.message

    def test_the_message_says_what_was_held_back(self, engine, store, source_items, radarr, paced):
        paced(max_adds=2)
        source_items["items"] = big_list(5)
        result = engine.run(add_list(store, name="Big").id)
        assert "3 queued for later" in result.message

    def test_off_by_default(self, engine, store, source_items, radarr):
        source_items["items"] = big_list(50)
        result = engine.run(add_list(store, name="Big").id)
        assert (result.added, result.queued) == (50, 0)


class TestDryRun:
    def test_a_dry_run_queues_nothing(self, engine, store, source_items, radarr, paced, database):
        paced(max_adds=2)
        source_items["items"] = big_list(5)
        job = add_list(store, name="Big")

        engine.run(job.id, dry_run=True)

        assert database.queue_counts() == {}

    def test_but_it_still_reports_what_would_wait(self, engine, store, source_items, radarr, paced):
        paced(max_adds=2)
        source_items["items"] = big_list(5)
        result = engine.run(add_list(store, name="Big").id, dry_run=True)
        assert result.queued == 3

    def test_a_dry_run_does_not_consume_the_window(self, engine, store, source_items, radarr, paced):
        paced(max_adds=10)
        source_items["items"] = big_list(5)
        engine.run(add_list(store, name="Preview").id, dry_run=True)

        assert engine.allowance(store.config) == 10


class TestDraining:
    def test_the_queue_is_released_on_the_next_window(
        self, engine, store, source_items, radarr, paced, database
    ):
        paced(max_adds=10)
        source_items["items"] = big_list(25)
        job = add_list(store, name="Big")
        engine.run(job.id)
        assert len(radarr["added"]) == 10

        # A new window: nothing counted against it yet.
        database.prune_add_events(0)
        released = engine.drain(10)

        assert released == 10
        assert len(radarr["added"]) == 20
        assert database.queue_counts()[job.id] == 5

    def test_draining_repeatedly_empties_the_queue(
        self, engine, store, source_items, radarr, paced, database
    ):
        paced(max_adds=10)
        source_items["items"] = big_list(25)
        job = add_list(store, name="Big")
        engine.run(job.id)

        for _ in range(5):
            database.prune_add_events(0)
            engine.drain(10)

        assert database.queue_counts() == {}
        assert len(radarr["added"]) == 25

    def test_every_title_arrives_exactly_once(
        self, engine, store, source_items, radarr, paced, database
    ):
        paced(max_adds=7)
        source_items["items"] = big_list(30)
        engine.run(add_list(store, name="Big").id)
        for _ in range(6):
            database.prune_add_events(0)
            engine.drain(7)

        ids = [entry[0] for entry in radarr["added"]]
        assert sorted(ids) == list(range(1, 31))
        assert len(ids) == len(set(ids))

    def test_a_title_added_meanwhile_is_not_added_twice(
        self, engine, store, source_items, radarr, paced, database
    ):
        """Someone may add it by hand, or another list may get there first."""
        paced(max_adds=2)
        source_items["items"] = big_list(5)
        job = add_list(store, name="Big")
        engine.run(job.id)

        radarr["library"] = {3, 4, 5}
        database.prune_add_events(0)
        engine.drain(10)

        assert 3 not in [entry[0] for entry in radarr["added"]]
        assert database.queue_counts() == {}

    def test_the_allowance_bounds_the_drain(
        self, engine, store, source_items, radarr, paced, database
    ):
        paced(max_adds=10)
        source_items["items"] = big_list(50)
        engine.run(add_list(store, name="Big").id)

        database.prune_add_events(0)
        assert engine.drain(engine.allowance(store.config)) == 10

    def test_the_window_is_shared_fairly_between_backlogs(
        self, engine, store, source_items, radarr, paced, database
    ):
        """A 1,000-title backlog must not starve a small list behind it."""
        paced(max_adds=2)
        source_items["items"] = big_list(20)
        big = add_list(store, name="Big")
        engine.run(big.id)

        source_items["items"] = big_list(6, start=500)
        small = add_list(store, name="Small")
        engine.run(small.id)

        database.prune_add_events(0)
        engine.drain(10)

        counts = database.queue_counts()
        assert counts[big.id] < 18 and counts[small.id] < 6

    def test_a_deleted_lists_queue_is_dropped(
        self, engine, store, source_items, radarr, paced, database
    ):
        paced(max_adds=1)
        source_items["items"] = big_list(5)
        job = add_list(store, name="Doomed")
        engine.run(job.id)

        store.mutate(lambda c: setattr(c, "lists", []))
        engine.drain(10)

        assert database.queue_counts() == {}

    def test_a_disabled_list_keeps_its_queue_but_adds_nothing(
        self, engine, store, source_items, radarr, paced, database
    ):
        paced(max_adds=1)
        source_items["items"] = big_list(5)
        job = add_list(store, name="Paused")
        engine.run(job.id)
        before = len(radarr["added"])

        store.mutate(lambda c: setattr(c.find_list(job.id), "enabled", False))
        database.prune_add_events(0)
        engine.drain(10)

        assert len(radarr["added"]) == before
        assert database.queue_counts()[job.id] == 4

    def test_draining_an_empty_queue_is_free(self, engine, database):
        assert engine.drain(10) == 0

    def test_a_drained_batch_is_recorded_in_history(
        self, engine, store, source_items, radarr, paced, database
    ):
        paced(max_adds=2)
        source_items["items"] = big_list(5)
        job = add_list(store, name="Big")
        engine.run(job.id)

        database.prune_add_events(0)
        engine.drain(2)

        latest = database.recent_runs(limit=1, list_id=job.id)[0]
        assert latest["added"] == 2
        assert "Trickle" in latest["message"]


class TestFailuresDoNotBlockTheQueue:
    def test_a_failing_title_does_not_stall_everything_behind_it(
        self, engine, store, source_items, radarr, paced, database
    ):
        paced(max_adds=2)
        source_items["items"] = big_list(6)
        job = add_list(store, name="Big")
        engine.run(job.id)

        # Title 3 cannot be resolved by Radarr's metadata service.
        radarr["unresolvable"] = {3}
        for _ in range(3):
            database.prune_add_events(0)
            engine.drain(2)

        assert database.queue_counts() == {}
        assert 3 not in [entry[0] for entry in radarr["added"]]
        assert {4, 5, 6}.issubset({entry[0] for entry in radarr["added"]})


class TestAllowance:
    def test_unlimited_when_off(self, engine, store):
        assert engine.allowance(store.config) == -1

    def test_full_when_nothing_has_been_added(self, engine, store, paced):
        paced(max_adds=10)
        assert engine.allowance(store.config) == 10

    def test_it_shrinks_as_titles_go_out(self, engine, store, source_items, radarr, paced):
        paced(max_adds=10)
        source_items["items"] = big_list(4)
        engine.run(add_list(store, name="Some").id)
        assert engine.allowance(store.config) == 6

    def test_it_never_goes_negative(self, engine, store, source_items, radarr, paced, database):
        paced(max_adds=10)
        source_items["items"] = big_list(4)
        engine.run(add_list(store, name="Some").id)
        store.mutate(lambda c: setattr(c.pacing, "max_adds", 2))
        assert engine.allowance(store.config) == 0


class TestQueueHousekeeping:
    def test_re_running_a_list_does_not_duplicate_the_queue(
        self, engine, store, source_items, radarr, paced, database
    ):
        paced(max_adds=1)
        source_items["items"] = big_list(5)
        job = add_list(store, name="Big")
        engine.run(job.id)
        engine.run(job.id)

        # 5 titles, at most 4 of them ever queued, however many times it runs.
        assert database.queue_counts()[job.id] <= 4

    def test_stale_entries_expire(self, database):
        database.enqueue("list-1", "movie", [(1, "", "Old", 1999)])
        assert database.queue_expire(0) == 1
        assert database.queue_counts() == {}

    def test_clearing_one_list_leaves_the_others(self, database):
        database.enqueue("a", "movie", [(1, "", "One", None)])
        database.enqueue("b", "movie", [(2, "", "Two", None)])
        database.queue_clear("a")
        assert set(database.queue_counts()) == {"b"}

    def test_shows_and_movies_queue_separately(self, database):
        database.enqueue("a", "movie", [(1, "", "Film", None)])
        database.enqueue("a", "show", [(1, "", "Series", None)])
        assert database.queue_counts()["a"] == 2
