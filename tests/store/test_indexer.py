import os
import shutil
import tempfile

import pytest
from hypothesis import given, settings as hyp_settings, strategies as st

from novelizer.canon.event_store import EventStore
from novelizer.canon.events import (
    ArcDeclared, ArcResolved, ChapterBriefDrafted, ChapterBriefFulfilled,
    ChapterBriefSuperseded, EventType, PromiseMade, PromisePaid,
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


async def test_lag_counts_in_sql_without_hydrating_pending_rows(stack, tmp_path):
    events, proj, read, store, _ = stack
    await seed(events, proj)
    spy = CountingEventStore(events)
    indexer = CanonIndexer(spy, read, store, str(tmp_path / "spy_cursor.json"))

    assert await indexer.lag() == 9
    assert spy.count_since_calls == 1
    assert spy.events_since_calls == 0  # a backlog of 10k events is a COUNT, not 10k rows


async def test_poison_event_is_skipped_after_the_configured_attempt_budget(stack, tmp_path, caplog):
    """The whole-room deadlock regression. Under the strict background gate a
    cursor pinned on a permanently-failing event freezes every agent forever,
    so the projector has to give up on that event and move past it."""
    events, proj, read, store, _ = stack
    await seed_chapters(events, proj, "ch1", "ch2", "ch3")
    emb = PoisonEmbeddingStore(store, {"ch2": None})
    indexer = CanonIndexer(events, read, emb, str(tmp_path / "poison.json"), poison_skip_after=3)

    assert await indexer.catch_up() == 1  # ch1 indexed, then ch2 fails (attempt 1)
    assert await indexer.catch_up() == 0  # ch2 fails again (attempt 2)
    caplog.clear()
    with caplog.at_level("ERROR"):
        # Attempt 3 exhausts the budget: ch2 is abandoned and ch3 -- which had
        # been stuck behind it -- is indexed in the same pass. A skipped event
        # is not a processed one, so only ch3 counts here.
        assert await indexer.catch_up() == 1
    assert [r for r in caplog.records if r.levelname == "ERROR"], \
        "abandoning an event is data loss; it must be logged at ERROR, not warned about"

    assert emb.attempts["ch2"] == 3
    assert emb.attempts["ch3"] == 1

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
