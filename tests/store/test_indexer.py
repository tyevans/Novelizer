import asyncio
import contextlib
import os
import shutil
import tempfile

import pytest
from hypothesis import given, settings as hyp_settings, strategies as st

from novelizer.canon.event_store import EventStore
from novelizer.canon.events import (
    ArcDeclared, ArcResolved, ChapterBriefDrafted, ChapterBriefFulfilled,
    ChapterBriefSuperseded, ChapterRevised, EventType, PromiseMade, PromisePaid,
    SecretCreated, ThemeIntroduced, ThreadPlanted,
)
from novelizer.canon.projector import Projector
from novelizer.canon.read_store import ReadStore
from novelizer.store.embeddings import EmbeddingStore
from novelizer.store.indexer import CanonIndexer
from novelizer.store.models import Chapter, Character, WorldEntry
from tests.conftest import FakeEmbeddingFunction


@pytest.fixture
async def stack(tmp_path):
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    events = EventStore(path); await events.init()
    proj = Projector(events, path); await proj.init()
    read = ReadStore(path); await read.init()
    store = EmbeddingStore(str(tmp_path / "emb"), embedding_function=FakeEmbeddingFunction())
    indexer = CanonIndexer(events, read, store, str(tmp_path / "cursor.json"))
    yield events, proj, read, store, indexer
    await read.close(); await proj.close(); await events.close()
    os.unlink(path)


async def seed(events, proj):
    await events.append(EventType.CHAPTER_CREATED, "ch1",
                        Chapter(id="ch1", title="One", prose="The bell rang."))
    await events.append(EventType.CHARACTER_CREATED, "mara",
                        Character(id="mara", name="Mara"))
    await events.append(EventType.WORLD_ENTRY_CREATED, "w1",
                        WorldEntry(id="w1", title="Bell Cult", body="dusk"))
    await events.append(EventType.THREAD_PLANTED, "t1", ThreadPlanted(id="t1", name="Curse"))
    await events.append(EventType.SECRET_CREATED, "s1", SecretCreated(id="s1", title="Scar"))
    await events.append(EventType.THEME_INTRODUCED, "th1", ThemeIntroduced(id="th1", title="Memory"))
    await events.append(EventType.PROMISE_MADE, "the-sealed-letter",
                        PromiseMade(id="the-sealed-letter", name="The Sealed Letter", description="bell wax seal"))
    await events.append(EventType.CHAPTER_BRIEF_DRAFTED, "b1",
                        ChapterBriefDrafted(brief_id="b1", target_ordinal=2, goal="bell tolls", synopsis="dusk"))
    await events.append(EventType.ARC_DECLARED, "arc1",
                        ArcDeclared(arc_id="arc1", character_id="mara", arc_type="positive", lie="bells lie"))
    await proj.catch_up()


async def seed_chapters(events, proj, *chapter_ids):
    for chapter_id in chapter_ids:
        await events.append(EventType.CHAPTER_CREATED, chapter_id,
                            Chapter(id=chapter_id, title=chapter_id, prose="The bell rang."))
    await proj.catch_up()


class AlwaysFailingEmbeddingStore:
    """Embed endpoint that is down for good: every call raises, so no event
    ever indexes successfully. The permanent-failure end of the poison ladder."""

    def __getattr__(self, name):
        raise RuntimeError("endpoint down")


class PoisonEmbeddingStore:
    """Wraps a real EmbeddingStore and fails chosen aggregates' upserts.

    `failures` maps aggregate id -> how many upserts raise before that record
    starts succeeding; None means every upsert raises, i.e. a genuine poison
    record. `attempts` tallies upserts per aggregate id, which is what the
    poison-skip tests assert on -- "skipped after exactly N attempts" is a
    claim about how many times the projector retried, not about log wording.
    """

    def __init__(self, real, failures: dict[str, int | None]) -> None:
        self._real = real
        self._failures = failures
        self.attempts: dict[str, int] = {}

    def __getattr__(self, name):
        attr = getattr(self._real, name)
        if not name.startswith("upsert_"):
            return attr

        async def guarded(record, *args, **kwargs):
            key = getattr(record, "id", record)
            self.attempts[key] = self.attempts.get(key, 0) + 1
            if key in self._failures:
                budget = self._failures[key]
                if budget is None or self.attempts[key] <= budget:
                    raise RuntimeError("embed endpoint refused this record")
            return await attr(record, *args, **kwargs)

        return guarded


class CountingEventStore:
    """Delegates to a real EventStore, tallying which query each caller reached
    for. lag() must count in SQL rather than hydrating every pending row only
    to take its len()."""

    def __init__(self, real) -> None:
        self._real = real
        self.events_since_calls = 0
        self.count_since_calls = 0

    async def events_since(self, *args, **kwargs):
        self.events_since_calls += 1
        return await self._real.events_since(*args, **kwargs)

    async def count_since(self, *args, **kwargs):
        self.count_since_calls += 1
        return await self._real.count_since(*args, **kwargs)


# --- Phase 5: parallel-drain probes -----------------------------------------
#
# Under the strict background gate (Phase 4) every agent waits on the drain, so
# the sequential catch_up loop is now the room's critical path and must go
# parallel: dedupe by aggregate, partition by aggregate, drain partitions
# concurrently, and advance the cursor only over the longest contiguous success
# prefix. These probes let the tests below observe the concurrency and dedup a
# serial loop cannot produce.


class ConcurrencyProbeEmbeddingStore:
    """Wraps a real EmbeddingStore, recording per-aggregate upsert counts and
    the PEAK number of upserts in flight at once. A serial drain never sees
    peak > 1; a drain that partitions by aggregate and runs partitions under
    pool permits does. Each upsert yields (`asyncio.sleep(0)`) after registering
    itself, so overlapping calls are observed deterministically by scheduling
    order rather than by racing wall-clock time."""

    def __init__(self, real) -> None:
        self._real = real
        self.calls: dict[str, int] = {}
        self.live = 0
        self.peak = 0

    def __getattr__(self, name):
        attr = getattr(self._real, name)
        if not name.startswith("upsert_"):
            return attr

        async def probed(record, *args, **kwargs):
            key = getattr(record, "id", record)
            self.calls[key] = self.calls.get(key, 0) + 1
            self.live += 1
            self.peak = max(self.peak, self.live)
            try:
                await asyncio.sleep(0)
                return await attr(record, *args, **kwargs)
            finally:
                self.live -= 1

        return probed


class BarrierEmbeddingStore:
    """Holds every upsert until `parties` of them are in flight at once, then
    releases them together. A serial catch_up can never get two upserts in
    flight simultaneously, so `all_entered` never fires and a test awaiting it
    fails on its timeout -- which is exactly the red state a serial loop should
    produce for the claim 'different aggregates drain concurrently'."""

    def __init__(self, real, parties: int) -> None:
        self._real = real
        self._parties = parties
        self.entered = 0
        self.all_entered = asyncio.Event()
        self._release = asyncio.Event()

    def __getattr__(self, name):
        attr = getattr(self._real, name)
        if not name.startswith("upsert_"):
            return attr

        async def gated(record, *args, **kwargs):
            self.entered += 1
            if self.entered >= self._parties:
                self.all_entered.set()
            await self._release.wait()
            return await attr(record, *args, **kwargs)

        return gated

    def release(self) -> None:
        self._release.set()


class SpyPool:
    """Minimal AdaptivePool stand-in for the drain. Enforces a hard concurrency
    ceiling (`limit`) like the real pool's `_limit`, records the peak permits
    held at once, and tallies the AIMD signals the drain feeds back, so a test
    can assert the 429 -> note_rate_limited / clean -> note_success mapping the
    scheduler already uses (agent_kit/scheduler.py `_run`). agent_kit stays
    generic: the drain must draw from a pool it duck-types, not this class."""

    def __init__(self, limit: int) -> None:
        self._limit = limit
        self._sem = asyncio.Semaphore(limit)
        self.live = 0
        self.peak = 0
        self.rate_limited = 0
        self.successes = 0

    @contextlib.asynccontextmanager
    async def slot(self):
        async with self._sem:
            self.live += 1
            self.peak = max(self.peak, self.live)
            try:
                yield
            finally:
                self.live -= 1

    def note_rate_limited(self) -> None:
        self.rate_limited += 1

    def note_success(self) -> None:
        self.successes += 1


class RateLimitError(Exception):
    """Named so agent_kit.base._is_rate_limit_error matches it by class name --
    the provider-agnostic path the scheduler already relies on. The drain must
    map this, and only this, to pool.note_rate_limited()."""


async def test_backfill_indexes_every_kind(stack):
    events, proj, read, store, indexer = stack
    await seed(events, proj)
    processed = await indexer.catch_up()
    assert processed == 9
    hits = await store.search("bell", n=20)
    assert {h.kind for h in hits} == {
        "chapter", "character", "world", "thread", "secret", "theme",
        "promise", "brief", "arc",
    }


async def test_catch_up_is_incremental_and_idempotent(stack):
    events, proj, read, store, indexer = stack
    await seed(events, proj)
    assert await indexer.catch_up() == 9
    assert await indexer.catch_up() == 0  # cursor persisted, nothing new
    await events.append(EventType.CHAPTER_CREATED, "ch2",
                        Chapter(id="ch2", title="Two", prose="More prose."))
    await proj.catch_up()
    assert await indexer.catch_up() == 1


async def test_lag_reports_indexable_events_not_yet_caught_up(stack):
    events, proj, read, store, indexer = stack
    await seed(events, proj)
    assert await indexer.lag() == 9  # nothing indexed yet
    await indexer.catch_up()
    assert await indexer.lag() == 0  # fully caught up

    await events.append(EventType.CHAPTER_CREATED, "ch2",
                        Chapter(id="ch2", title="Two", prose="More prose."))
    await proj.catch_up()
    assert await indexer.lag() == 1  # one new indexable event, cursor untouched
    assert await indexer.lag() == 1  # read-only: calling again doesn't change it


async def test_lag_does_not_mutate_cursor_or_index(stack, tmp_path):
    events, proj, read, store, indexer = stack
    await seed(events, proj)
    await indexer.lag()  # must not advance the cursor
    fresh = CanonIndexer(events, read, store, str(tmp_path / "cursor.json"))
    assert await fresh.catch_up() == 9  # everything still unindexed


async def test_cursor_survives_new_indexer_instance(stack, tmp_path):
    events, proj, read, store, indexer = stack
    await seed(events, proj)
    await indexer.catch_up()
    fresh = CanonIndexer(events, read, store, str(tmp_path / "cursor.json"))
    assert await fresh.catch_up() == 0


async def test_embed_failure_leaves_cursor_for_retry(stack, tmp_path):
    events, proj, read, store, indexer = stack
    await seed(events, proj)

    class Boom:
        def __getattr__(self, name):
            raise RuntimeError("endpoint down")

    broken = CanonIndexer(events, read, Boom(), str(tmp_path / "cursor2.json"))
    assert await broken.catch_up() == 0  # swallowed, not raised
    assert await indexer.catch_up() == 9  # untouched cursor path still backfills


# --- projection lag is a retry-later, never a silent success ----------------


async def test_projection_lag_does_not_advance_the_cursor(stack, tmp_path):
    """A read-store row that has not been projected YET is not "nothing to
    index", it is "not ready yet". Treating it as success advanced the cursor
    past an event whose embedding was never written -- permanent, silent loss
    (observed in production: embed_cursor.json parked at 97 of 117 events, an
    index of zero documents, and no backlog left to tell anyone)."""
    events, proj, read, store, indexer = stack
    await events.append(EventType.CHAPTER_CREATED, "ch1",
                        Chapter(id="ch1", title="One", prose="The bell rang."))
    # Deliberately NO proj.catch_up(): the chapters table has no ch1 row yet.

    assert await indexer.catch_up() == 0
    assert indexer._load_cursor() == 0   # never past an event that wasn't indexed
    assert await indexer.lag() == 1      # and the backlog still says so
    assert await store.document_count() == 0

    await proj.catch_up()                # the projection catches up
    assert await indexer.catch_up() == 1
    assert indexer._load_cursor() == 1
    assert {h.id for h in await store.search("bell", kinds=["chapter"])} == {"ch1"}


async def test_a_never_projected_event_is_still_abandoned_after_the_budget(stack, tmp_path, caplog):
    """The other half of the contract: retry-later must stay BOUNDED. An event
    whose aggregate never appears in the read store (a genuinely bad event, not
    a lagging one) would otherwise wedge the drain forever, and under the strict
    background gate a wedged drain pauses every agent forever. The poison ladder
    already owns that decision, so retrying via a raise inherits it unchanged."""
    events, proj, read, store, _ = stack
    await events.append(EventType.CHAPTER_CREATED, "ghost",
                        Chapter(id="ghost", title="Never Projected", prose="The bell rang."))
    indexer = CanonIndexer(events, read, store, str(tmp_path / "lag.json"),
                           poison_skip_after=3)

    assert await indexer.catch_up() == 0
    assert indexer._load_cursor() == 0
    assert await indexer.catch_up() == 0
    assert indexer._load_cursor() == 0

    caplog.clear()
    with caplog.at_level("ERROR"):
        assert await indexer.catch_up() == 0  # budget spent: abandoned, not processed
    assert [r for r in caplog.records if r.levelname == "ERROR"], \
        "abandoning an event is data loss; it must be logged at ERROR"
    assert indexer._load_cursor() == 1  # cursor jumped past it: the drain is free
    assert await indexer.lag() == 0


async def test_promise_brief_arc_kind_filter_isolates_each(stack):
    events, proj, read, store, indexer = stack
    await seed(events, proj)
    await indexer.catch_up()
    assert {h.id for h in await store.search("bell", kinds=["promise"], n=20)} == {"the-sealed-letter"}
    assert {h.id for h in await store.search("bell", kinds=["brief"], n=20)} == {"b1"}
    assert {h.id for h in await store.search("bell", kinds=["arc"], n=20)} == {"arc1"}


async def test_catch_up_never_raises_even_if_event_store_fails(stack, tmp_path):
    events, proj, read, store, indexer = stack

    class BrokenEvents:
        async def events_since(self, *a, **k): raise RuntimeError("database is locked")

    broken = CanonIndexer(BrokenEvents(), read, store, str(tmp_path / "c3.json"))
    assert await broken.catch_up() == 0


async def test_superseded_world_entry_removed_from_index(stack):
    events, proj, read, store, indexer = stack
    await seed(events, proj)
    await indexer.catch_up()
    hits = await store.search("bell", kinds=["world"], n=20)
    assert any(h.id == "w1" for h in hits)

    # aggregate_id is the entity the event concerns for indexing purposes --
    # here, the entry being superseded (w1). The payload is the replacement
    # record (w2), whose own upsert is driven by its own WORLD_ENTRY_CREATED
    # trail; this event's job is to retire w1 from the active list/index.
    await events.append(
        EventType.WORLD_ENTRY_SUPERSEDED, "w1",
        WorldEntry(id="w2", title="Bell Cult Revised", body="dawn", supersedes_id="w1"),
    )
    await proj.catch_up()
    await indexer.catch_up()

    assert store._world.get(ids=["w1"])["ids"] == []


async def test_supersede_via_real_retconner_convention_removes_old_embedding(stack):
    events, proj, read, store, indexer = stack
    await seed(events, proj)
    await indexer.catch_up()
    assert store._world.get(ids=["w1"])["ids"] == ["w1"]
    new = WorldEntry(id="w2", title="Bell Cult, Revised", body="They ring at dawn.", supersedes_id="w1")
    await events.append(EventType.WORLD_ENTRY_SUPERSEDED, "w2", new)
    await proj.catch_up()
    await indexer.catch_up()
    assert store._world.get(ids=["w1"])["ids"] == []
    assert store._world.get(ids=["w2"])["ids"] == ["w2"]


async def test_promise_paid_refreshes_vector_with_terminal_state(stack):
    events, proj, read, store, indexer = stack
    await seed(events, proj)
    await indexer.catch_up()

    await events.append(EventType.PROMISE_PAID, "the-sealed-letter",
                        PromisePaid(id="the-sealed-letter", chapter_id="ch1"))
    await proj.catch_up()
    processed = await indexer.catch_up()
    assert processed == 1

    doc = store._promises.get(ids=["the-sealed-letter"])["documents"][0]
    assert "state: paid" in doc


async def test_brief_superseded_refreshes_vector_with_terminal_status(stack):
    events, proj, read, store, indexer = stack
    await seed(events, proj)
    await indexer.catch_up()

    await events.append(EventType.CHAPTER_BRIEF_SUPERSEDED, "b1",
                        ChapterBriefSuperseded(brief_id="b1"))
    await proj.catch_up()
    processed = await indexer.catch_up()
    assert processed == 1

    doc = store._briefs.get(ids=["b1"])["documents"][0]
    assert "status: superseded" in doc


async def test_brief_fulfilled_refreshes_vector_with_terminal_status(stack):
    events, proj, read, store, indexer = stack
    await seed(events, proj)
    await indexer.catch_up()

    await events.append(EventType.CHAPTER_BRIEF_FULFILLED, "b1",
                        ChapterBriefFulfilled(brief_id="b1", chapter_id="ch1"))
    await proj.catch_up()
    processed = await indexer.catch_up()
    assert processed == 1

    doc = store._briefs.get(ids=["b1"])["documents"][0]
    assert "status: fulfilled" in doc


async def test_unknown_kind_is_skipped_with_warning_not_crash(stack, caplog):
    # A future event-type prefix that isn't mapped to a known kind must not
    # be silently treated as an arc by a trailing catch-all `else`; it
    # should be logged and skipped instead of raising.
    events, proj, read, store, indexer = stack
    import novelizer.store.indexer as indexer_module
    indexer_module._PREFIX_TO_KIND["bogus"] = "bogus"
    try:
        with caplog.at_level("WARNING"):
            await indexer._index_one("bogus.something_happened", "x1")
    finally:
        del indexer_module._PREFIX_TO_KIND["bogus"]
    assert "bogus" in caplog.text.lower() or "unknown" in caplog.text.lower()


async def test_arc_resolved_refreshes_vector_with_outcome(stack):
    events, proj, read, store, indexer = stack
    await seed(events, proj)
    await indexer.catch_up()

    await events.append(EventType.ARC_RESOLVED, "arc1",
                        ArcResolved(arc_id="arc1", chapter_id="ch1", outcome="truth_embraced"))
    await proj.catch_up()
    processed = await indexer.catch_up()
    assert processed == 1

    doc = store._arcs.get(ids=["arc1"])["documents"][0]
    assert "resolved: truth_embraced" in doc


async def test_retired_world_entry_removed_from_index(stack):
    events, proj, read, store, indexer = stack
    from novelizer.canon.events import WorldEntryRetired

    await events.append(EventType.WORLD_ENTRY_CREATED, "w1",
                        WorldEntry(id="w1", title="Bell Cult", body="dusk bells"))
    await proj.catch_up()
    await indexer.catch_up()
    assert any(h.kind == "world" for h in await store.search("bell", n=20))

    await events.append(EventType.WORLD_ENTRY_RETIRED, "w1",
                        WorldEntryRetired(entry_id="w1", reason="redundant"))
    await proj.catch_up()
    await indexer.catch_up()
    # w1's vector was the only one in the index, so its removal empties the
    # index outright -- and search() now reports an empty index as unavailable
    # rather than answering it, so count the documents instead of querying.
    assert await store.document_count() == 0


async def test_lag_counts_in_sql_without_hydrating_pending_rows(stack, tmp_path):
    events, proj, read, store, _ = stack
    await seed(events, proj)
    spy = CountingEventStore(events)
    indexer = CanonIndexer(spy, read, store, str(tmp_path / "spy_cursor.json"))

    assert await indexer.lag() == 9
    assert spy.count_since_calls == 1
    assert spy.events_since_calls == 0  # a backlog of 10k events is a COUNT, not 10k rows


async def test_poison_event_is_skipped_after_the_configured_attempt_budget(stack, tmp_path, caplog):
    """The whole-room deadlock regression, now under PARALLEL drain. Under the
    strict background gate a cursor pinned on a permanently-failing event freezes
    every agent forever, so the projector must give up on that event and move
    past it.

    REWRITTEN for Phase 5 (was a serial-loop test). The old version asserted
    `attempts["ch3"] == 1` -- true only because the serial loop left ch3 stuck
    BEHIND poison ch2 until ch2 was abandoned. Under parallel drain ch3 is a
    different aggregate and embeds CONCURRENTLY on pass 1; because the cursor may
    not advance past failed ch2 (property C), ch3 sits beyond the barrier and is
    redundantly (idempotently) re-embedded each pass, so `attempts["ch3"]` climbs
    instead of staying 1. What is invariant, and what this test now pins: the
    cursor never passes ch2 until its budget is spent, ch2 is tried EXACTLY
    poison_skip_after times, and the backlog still drains to zero. The per-pass
    processed counts (1, 0, 1) are unchanged -- processed counts the contiguous
    success PREFIX in sequences, and ch3's early embed is beyond that prefix."""
    events, proj, read, store, _ = stack
    await seed_chapters(events, proj, "ch1", "ch2", "ch3")
    emb = PoisonEmbeddingStore(store, {"ch2": None})
    indexer = CanonIndexer(events, read, emb, str(tmp_path / "poison.json"), poison_skip_after=3)

    # Pass 1: ch1 (seq 1) is the whole contiguous success prefix -> processed 1,
    # cursor pinned at 1 by ch2's failure. ch3 (seq 3) embeds concurrently even
    # though the cursor cannot reach it -- the parallel behavior a serial loop
    # cannot show, and the direct evidence of concurrent partition drain here.
    assert await indexer.catch_up() == 1
    assert indexer._load_cursor() == 1
    assert {h.id for h in await store.search("bell", kinds=["chapter"], n=10)} == {"ch1", "ch3"}

    assert await indexer.catch_up() == 0  # ch2 fails again (attempt 2); cursor still 1
    assert indexer._load_cursor() == 1
    caplog.clear()
    with caplog.at_level("ERROR"):
        # Attempt 3 exhausts the budget: ch2 is abandoned and the cursor jumps
        # past it AND ch3 (already embedded), consuming seq 2 and 3 at once. A
        # skipped event is not a processed one, so only ch3's seq counts: 1.
        assert await indexer.catch_up() == 1
    assert [r for r in caplog.records if r.levelname == "ERROR"], \
        "abandoning an event is data loss; it must be logged at ERROR, not warned about"

    assert emb.attempts["ch2"] == 3  # exactly the budget, never a 4th try
    # ch3 was re-embedded on every pass it sat beyond the barrier: redundant but
    # idempotent, the accepted price of never advancing the cursor past a failure.
    assert emb.attempts["ch3"] >= 1

    # The cursor is past the poison event for good: never retried, no backlog.
    assert await indexer.catch_up() == 0
    assert emb.attempts["ch2"] == 3
    assert await indexer.lag() == 0


async def test_poison_skip_after_defaults_to_three_attempts(stack, tmp_path):
    events, proj, read, store, _ = stack
    await seed_chapters(events, proj, "ch1", "ch2", "ch3")
    emb = PoisonEmbeddingStore(store, {"ch2": None})
    indexer = CanonIndexer(events, read, emb, str(tmp_path / "default_budget.json"))

    for _ in range(4):
        await indexer.catch_up()

    assert emb.attempts["ch2"] == 3
    assert await indexer.lag() == 0


async def test_transient_failure_is_retried_rather_than_skipped(stack, tmp_path, caplog):
    """An embed endpoint that blips twice and recovers is not poison. The
    budget bounds consecutive failures, and the event still gets indexed if it
    succeeds on its last chance."""
    events, proj, read, store, _ = stack
    await seed_chapters(events, proj, "ch1", "ch2", "ch3")
    emb = PoisonEmbeddingStore(store, {"ch2": 2})
    indexer = CanonIndexer(events, read, emb, str(tmp_path / "transient.json"), poison_skip_after=3)

    caplog.clear()
    with caplog.at_level("ERROR"):
        assert await indexer.catch_up() == 1  # ch1 indexed, ch2 fails (attempt 1)
        assert await indexer.catch_up() == 0  # ch2 fails (attempt 2)
        assert await indexer.catch_up() == 2  # ch2 succeeds on attempt 3, then ch3
    assert not [r for r in caplog.records if r.levelname == "ERROR"]

    assert emb.attempts["ch2"] == 3
    assert {h.id for h in await store.search("bell", kinds=["chapter"], n=10)} == {"ch1", "ch2", "ch3"}


async def test_failure_counts_are_per_sequence_and_do_not_accumulate(stack, tmp_path, caplog):
    """Three different events each failing once must not add up to a skip: the
    budget is per sequence, and a sequence's tally clears when it succeeds. A
    single fleet-wide counter would abandon ch2 and ch3 here."""
    events, proj, read, store, _ = stack
    await seed_chapters(events, proj, "ch1", "ch2", "ch3")
    emb = PoisonEmbeddingStore(store, {"ch1": 1, "ch2": 1, "ch3": 1})
    indexer = CanonIndexer(events, read, emb, str(tmp_path / "alternating.json"), poison_skip_after=2)

    caplog.clear()
    with caplog.at_level("ERROR"):
        for _ in range(6):
            await indexer.catch_up()
    assert not [r for r in caplog.records if r.levelname == "ERROR"]

    assert emb.attempts == {"ch1": 2, "ch2": 2, "ch3": 2}  # one failure, one success each
    assert {h.id for h in await store.search("bell", kinds=["chapter"], n=10)} == {"ch1", "ch2", "ch3"}


async def test_catch_up_drains_the_backlog_even_when_every_event_fails(stack, tmp_path):
    """Nothing succeeds and nothing raises, yet the backlog still reaches zero
    -- the property the strict background gate stakes the whole room on."""
    events, proj, read, store, _ = stack
    await seed(events, proj)
    broken = CanonIndexer(events, read, AlwaysFailingEmbeddingStore(),
                          str(tmp_path / "deadlock.json"), poison_skip_after=2)

    for _ in range(9 * 3):
        await broken.catch_up()

    assert await broken.lag() == 0


@hyp_settings(deadline=None, max_examples=15)
@given(n_events=st.integers(min_value=1, max_value=6),
       skip_after=st.integers(min_value=1, max_value=4))
async def test_a_permanently_broken_index_always_drains_within_a_bounded_number_of_rounds(
    n_events, skip_after,
):
    """Liveness, generalized: whatever the backlog size and whatever the
    budget, repeated catch_up calls reach lag() == 0. n_events * (skip_after +
    1) rounds is a loose bound; the point is that a bound exists at all."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    workdir = tempfile.mkdtemp()
    events = EventStore(path)
    await events.init()
    proj = Projector(events, path)
    await proj.init()
    read = ReadStore(path)
    await read.init()
    try:
        for i in range(n_events):
            await events.append(EventType.CHAPTER_CREATED, f"ch{i}",
                                Chapter(id=f"ch{i}", title=str(i), prose="prose"))
        await proj.catch_up()

        indexer = CanonIndexer(events, read, AlwaysFailingEmbeddingStore(),
                               os.path.join(workdir, "cursor.json"),
                               poison_skip_after=skip_after)
        for _ in range(n_events * (skip_after + 1)):
            await indexer.catch_up()

        assert await indexer.lag() == 0
    finally:
        await read.close()
        await proj.close()
        await events.close()
        os.unlink(path)
        shutil.rmtree(workdir)


# --- Phase 5, property A: dedupe by aggregate within the pending window -----


async def test_multiple_events_for_one_aggregate_index_it_once_per_pass(stack, tmp_path):
    """Chapter 7 revised three times inside one pending window is four events,
    four sequences, ONE aggregate. The projector hydrates the CURRENT chapter
    from ReadStore regardless of which event triggered it, so re-embedding it
    four times is pure waste -- the drain must collapse the window to a single
    index operation. processed still counts all four consumed sequences, though:
    dedup is about work done, not about pretending events did not happen."""
    events, proj, read, store, _ = stack
    await events.append(EventType.CHAPTER_CREATED, "ch7",
                        Chapter(id="ch7", title="Seven", prose="v0 the bell rang"))
    for v in range(1, 4):
        await events.append(EventType.CHAPTER_REVISED, "ch7",
                            ChapterRevised(chapter_id="ch7", prose=f"v{v} the bell rang"))
    await proj.catch_up()

    probe = ConcurrencyProbeEmbeddingStore(store)
    indexer = CanonIndexer(events, read, probe, str(tmp_path / "dedup.json"))
    processed = await indexer.catch_up()

    assert probe.calls["ch7"] == 1  # one embed for the whole window, not four
    assert processed == 4           # all four sequences are consumed by that one op
    assert await indexer.lag() == 0


# --- Phase 5, property B: partitions for different aggregates drain concurrently


async def test_partitions_for_different_aggregates_drain_concurrently(stack, tmp_path):
    """Two distinct aggregates must both reach 'inside the embed call' before
    either finishes -- proof the drain runs partitions concurrently rather than
    one after another. The barrier only releases once BOTH upserts are in
    flight; a serial catch_up holds exactly one at a time, so `all_entered` never
    fires and the wait below times out. Concurrency is the point: under the
    strict gate the drain is the room's critical path, so a serial drain simply
    becomes the new source of idleness."""
    events, proj, read, store, _ = stack
    await seed_chapters(events, proj, "ch1", "ch2")
    barrier = BarrierEmbeddingStore(store, parties=2)
    indexer = CanonIndexer(events, read, barrier, str(tmp_path / "concurrent.json"),
                           drain_concurrency=4)

    task = asyncio.create_task(indexer.catch_up())
    try:
        await asyncio.wait_for(barrier.all_entered.wait(), timeout=2.0)
    finally:
        barrier.release()
    assert await task == 2


async def test_drain_fan_out_is_capped_by_drain_concurrency(stack, tmp_path):
    """drain_concurrency bounds how many partitions run at once independently of
    the pool -- so 1000 pending aggregates never spawn 1000 tasks. With no pool
    and a fan-out cap of 2, at most two embeds are ever in flight even with four
    pending aggregates."""
    events, proj, read, store, _ = stack
    await seed_chapters(events, proj, "ch1", "ch2", "ch3", "ch4")
    probe = ConcurrencyProbeEmbeddingStore(store)
    indexer = CanonIndexer(events, read, probe, str(tmp_path / "fanout.json"),
                           drain_concurrency=2)

    processed = await indexer.catch_up()
    assert processed == 4
    assert probe.peak == 2  # the fan-out cap bit; never a third concurrent embed


# --- Phase 5, property C: cursor == longest contiguous success prefix -------


async def test_cursor_advances_to_the_end_when_every_partition_succeeds(stack, tmp_path):
    events, proj, read, store, _ = stack
    await seed_chapters(events, proj, "ch1", "ch2", "ch3", "ch4", "ch5")
    indexer = CanonIndexer(events, read, store, str(tmp_path / "all_ok.json"))
    processed = await indexer.catch_up()
    assert indexer._load_cursor() == 5
    assert processed == 5


async def test_cursor_stops_just_before_the_first_failed_sequence(stack, tmp_path):
    """Sequences [1..5]; the aggregate at seq 3 fails, all others succeed. The
    cursor may advance only to 2. seq 4 and 5 embedded successfully (concurrent
    drain), but advancing past failed seq 3 would drop its embedding for good --
    a lost event, the one thing the poison ladder must never do silently -- so
    the cursor pins at 2 and seq 3,4,5 are retried next pass. poison_skip_after
    is large so nothing is abandoned within this single pass."""
    events, proj, read, store, _ = stack
    await seed_chapters(events, proj, "ch1", "ch2", "ch3", "ch4", "ch5")
    emb = PoisonEmbeddingStore(store, {"ch3": None})
    indexer = CanonIndexer(events, read, emb, str(tmp_path / "prefix.json"),
                           poison_skip_after=99)
    processed = await indexer.catch_up()

    assert indexer._load_cursor() == 2  # just before seq 3
    assert processed == 2
    # ch4 and ch5 DID embed this pass even though the cursor never reached them:
    # later partitions are not blocked by an earlier failure, only the cursor is.
    assert {h.id for h in await store.search("bell", kinds=["chapter"], n=10)} >= {"ch1", "ch2", "ch4", "ch5"}


async def test_cursor_does_not_move_at_all_when_the_first_sequence_fails(stack, tmp_path):
    events, proj, read, store, _ = stack
    await seed_chapters(events, proj, "ch1", "ch2", "ch3")
    emb = PoisonEmbeddingStore(store, {"ch1": None})
    indexer = CanonIndexer(events, read, emb, str(tmp_path / "first_fail.json"),
                           poison_skip_after=99)
    processed = await indexer.catch_up()
    assert indexer._load_cursor() == 0
    assert processed == 0


@hyp_settings(deadline=None, max_examples=30)
@given(outcomes=st.lists(st.booleans(), min_size=1, max_size=8))
async def test_cursor_is_exactly_the_longest_contiguous_success_prefix(outcomes):
    """Property C, the safety centerpiece. For ANY per-sequence success/failure
    pattern across distinct aggregates, one parallel drain pass advances the
    cursor to the largest K such that every sequence <= K succeeded -- never past
    a failed sequence (that loses its embedding), never short of a runnable
    prefix (that re-embeds needlessly forever). poison_skip_after is large so no
    sequence is abandoned within this single pass, isolating the prefix rule from
    the poison rule."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    workdir = tempfile.mkdtemp()
    events = EventStore(path)
    await events.init()
    proj = Projector(events, path)
    await proj.init()
    read = ReadStore(path)
    await read.init()
    store = EmbeddingStore(os.path.join(workdir, "emb"), embedding_function=FakeEmbeddingFunction())
    try:
        ids = [f"ch{i}" for i in range(1, len(outcomes) + 1)]
        await seed_chapters(events, proj, *ids)
        failing = {ids[i]: None for i, ok in enumerate(outcomes) if not ok}
        emb = PoisonEmbeddingStore(store, failing)
        indexer = CanonIndexer(events, read, emb, os.path.join(workdir, "cursor.json"),
                               poison_skip_after=99)
        await indexer.catch_up()

        expected = 0  # length of the leading run of True in outcomes (seqs are 1..N)
        for ok in outcomes:
            if not ok:
                break
            expected += 1
        assert indexer._load_cursor() == expected

        # Every succeeding aggregate is embedded on THIS pass, even those sitting
        # beyond the first failure -- concurrent partitions are not blocked by an
        # earlier one, only the cursor is. A serial loop breaks at the first
        # failure and never reaches the later successes, so this is where the
        # property distinguishes parallel drain from the loop it replaces.
        should_be_embedded = {ids[i] for i, ok in enumerate(outcomes) if ok}
        if should_be_embedded:
            embedded = {h.id for h in await store.search("bell", n=20)}
            assert should_be_embedded <= embedded
        else:
            # Every aggregate failed, so nothing was embedded at all. An index
            # with zero documents cannot be queried (search() reports itself
            # unavailable), so the document count carries the same claim.
            assert await store.document_count() == 0
    finally:
        await read.close()
        await proj.close()
        await events.close()
        os.unlink(path)
        shutil.rmtree(workdir)


# --- Phase 5, property D: poison-skip unblocks the prefix under parallelism --


async def test_a_permanently_failing_aggregate_is_skipped_so_the_prefix_advances(stack, tmp_path, caplog):
    """Property D at the drain level -- the Phase-4-gate deadlock-avoidance
    property. A single aggregate that fails every pass sits at seq 3 with good
    work both behind AND ahead of it. Under the strict gate a cursor wedged at
    seq 2 forever pauses every agent forever, so after poison_skip_after passes
    seq 3 must be abandoned and the cursor jump past it, draining ch4 and ch5."""
    events, proj, read, store, _ = stack
    await seed_chapters(events, proj, "ch1", "ch2", "ch3", "ch4", "ch5")
    emb = PoisonEmbeddingStore(store, {"ch3": None})
    indexer = CanonIndexer(events, read, emb, str(tmp_path / "unblock.json"),
                           poison_skip_after=3)

    # Passes 1-2: seq 3 fails but its budget is not spent; the cursor stays
    # wedged at 2 even though ch4/ch5 embed successfully on every pass. That
    # early embed -- ch4/ch5 indexed on pass 1 while the cursor is still 2 and
    # ch3 is not yet skipped -- is the parallel behavior; a serial loop breaks at
    # ch3 and never reaches ch4/ch5 until ch3 is abandoned on pass 3.
    await indexer.catch_up()
    assert indexer._load_cursor() == 2
    assert {"ch4", "ch5"} <= {h.id for h in await store.search("bell", kinds=["chapter"], n=10)}
    await indexer.catch_up()
    assert indexer._load_cursor() == 2

    caplog.clear()
    with caplog.at_level("ERROR"):
        await indexer.catch_up()  # attempt 3 exhausts the budget on seq 3
    assert [r for r in caplog.records if r.levelname == "ERROR"]

    assert emb.attempts["ch3"] == 3  # tried exactly the budget, never more
    assert await indexer.lag() == 0  # cursor jumped past 3 and drained 4,5
    assert {h.id for h in await store.search("bell", kinds=["chapter"], n=10)} == {"ch1", "ch2", "ch4", "ch5"}

    # ch3 abandoned for good: never retried, no lingering backlog.
    await indexer.catch_up()
    assert emb.attempts["ch3"] == 3


# --- Phase 5: shared-pool integration (ceiling + AIMD signal mapping) --------


async def test_drain_units_respect_the_shared_pool_ceiling(stack, tmp_path):
    """The drain's per-aggregate work draws permits from the SAME AdaptivePool
    the scheduler uses, so agents + drain share one endpoint ceiling and one
    AIMD controller -- the property that does not hold today (two independent LLM
    consumers, no shared limit). With four pending aggregates and a pool limit of
    2, at most two embeds run at once even though drain_concurrency would allow
    all four -- the pool, not the fan-out cap, is the binding constraint here."""
    events, proj, read, store, _ = stack
    await seed_chapters(events, proj, "ch1", "ch2", "ch3", "ch4")
    probe = ConcurrencyProbeEmbeddingStore(store)
    pool = SpyPool(limit=2)
    indexer = CanonIndexer(events, read, probe, str(tmp_path / "pooled.json"),
                           pool=pool, drain_concurrency=4)

    processed = await indexer.catch_up()
    assert processed == 4
    assert pool.peak == 2   # the shared ceiling actually bit
    assert probe.peak <= 2  # no embed ran without a permit


async def test_drain_maps_a_rate_limit_to_note_rate_limited(stack, tmp_path):
    """A 429 during the drain must feed the shared pool exactly the congestion
    signal the scheduler feeds on an agent-run 429, so AIMD backoff covers the
    drain too -- the drain is a first-class consumer of the one endpoint."""
    events, proj, read, store, _ = stack
    await seed_chapters(events, proj, "ch1")

    class RateLimited:
        def __getattr__(self, name):
            async def boom(*args, **kwargs):
                raise RateLimitError("429 slow down")
            return boom

    pool = SpyPool(limit=4)
    indexer = CanonIndexer(events, read, RateLimited(), str(tmp_path / "rl.json"), pool=pool)
    await indexer.catch_up()

    assert pool.rate_limited == 1
    assert pool.successes == 0


async def test_drain_maps_a_clean_unit_to_note_success(stack, tmp_path):
    events, proj, read, store, _ = stack
    await seed_chapters(events, proj, "ch1", "ch2")
    pool = SpyPool(limit=4)
    indexer = CanonIndexer(events, read, store, str(tmp_path / "ok.json"), pool=pool)
    await indexer.catch_up()

    assert pool.successes == 2  # one clean signal per successfully drained aggregate
    assert pool.rate_limited == 0


async def test_drain_feeds_no_signal_on_a_plain_crash(stack, tmp_path):
    """A malformed record or a bug is not congestion. Mirroring the scheduler, a
    non-429 crash feeds the pool NEITHER signal, so an ordinary failure never
    shrinks the fleet-wide ceiling every other consumer draws from."""
    events, proj, read, store, _ = stack
    await seed_chapters(events, proj, "ch1")

    class Boom:
        def __getattr__(self, name):
            async def boom(*args, **kwargs):
                raise RuntimeError("a bug, not a rate limit")
            return boom

    pool = SpyPool(limit=4)
    indexer = CanonIndexer(events, read, Boom(), str(tmp_path / "crash.json"), pool=pool)
    await indexer.catch_up()

    assert pool.rate_limited == 0
    assert pool.successes == 0
