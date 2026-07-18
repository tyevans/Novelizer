import json
import os
import tempfile
import pytest
from novelizer.canon.event_store import EventStore
from novelizer.canon.projector import Projector
from novelizer.canon.events import EventType
from novelizer.store.models import Chapter, WorldEntry, Character, DirectorSignal, SignalKind


@pytest.fixture
async def wired():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    events = EventStore(path)
    await events.init()
    proj = Projector(events, path)
    await proj.init()
    yield events, proj, path
    await proj.close()
    await events.close()
    os.unlink(path)


async def _chapter_rows(proj):
    cur = await proj._conn.execute("SELECT data FROM chapters ORDER BY rowid")
    return [json.loads(r[0]) for r in await cur.fetchall()]


async def _all_table_rows(proj):
    """Get all projection tables' data rows as dicts."""
    tables = {}
    for table in ("chapters", "world_entries", "characters", "director_signals", "proposals", "autonomy_state"):
        cur = await proj._conn.execute(f"SELECT data FROM {table} ORDER BY rowid")
        tables[table] = [json.loads(r[0]) for r in await cur.fetchall()]
    return tables


async def test_chapter_created_is_projected(wired):
    events, proj, _ = wired
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
    await proj.catch_up()
    rows = await _chapter_rows(proj)
    assert len(rows) == 1 and rows[0]["title"] == "One"


async def test_director_signal_consumed_flips_flag(wired):
    events, proj, _ = wired
    sig = DirectorSignal(id="s1", kind=SignalKind.seed, body="storm")
    await events.append(EventType.DIRECTOR_SIGNAL_CREATED, "s1", sig)
    await proj.catch_up()
    cur = await proj._conn.execute("SELECT consumed FROM director_signals WHERE id='s1'")
    assert (await cur.fetchone())[0] == 0
    await events.append(EventType.DIRECTOR_SIGNAL_CONSUMED, "s1", sig)
    await proj.catch_up()
    cur = await proj._conn.execute("SELECT consumed FROM director_signals WHERE id='s1'")
    assert (await cur.fetchone())[0] == 1


async def test_catch_up_advances_and_is_idempotent(wired):
    events, proj, _ = wired
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
    assert await proj.catch_up() == 1
    assert await proj.catch_up() == 1  # no new events, no-op
    assert len(await _chapter_rows(proj)) == 1


async def test_reprojecting_same_events_is_equivalent(wired):
    from novelizer.canon.autonomy import Proposal, AutonomyState, AutonomyLevel
    events, proj, path = wired
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
    await events.append(EventType.WORLD_ENTRY_CREATED, "w1", WorldEntry(id="w1", title="W", body="b"))
    await events.append(EventType.CHARACTER_CREATED, "ch1", Character(id="ch1", name="Mira"))
    # Append a world_entry.superseded event that supersedes the first world entry.
    await events.append(EventType.WORLD_ENTRY_SUPERSEDED, "w2", WorldEntry(id="w2", title="W2", body="b2", supersedes_id="w1"))
    # Append a proposal.created event to test proposals table projection.
    prop = Proposal(proposing_agent="author", target_event_type="chapter.created",
                     target_aggregate_id="c1", payload={"title": "One", "prose": "p"})
    await events.append(EventType.PROPOSAL_CREATED, prop.id, prop)
    # Append an autonomy.changed event to test autonomy_state table projection.
    au = AutonomyState(global_level=AutonomyLevel.gated_canon)
    await events.append(EventType.AUTONOMY_CHANGED, "singleton", au)
    await proj.catch_up()
    incremental = await _all_table_rows(proj)
    # Fresh projector over the same log, projecting from zero, yields the same rows.
    proj2 = Projector(events, path)
    await proj2.init()
    await proj2._reset_state()  # force last_sequence=0
    await proj2.catch_up()
    from_scratch = await _all_table_rows(proj2)
    await proj2.close()
    # Assert all six projection tables have identical rows.
    for table in ("chapters", "world_entries", "characters", "director_signals", "proposals", "autonomy_state"):
        assert incremental[table] == from_scratch[table], f"Table {table} mismatch between incremental and from-scratch"


async def test_proposal_created_is_projected_open(wired):
    from novelizer.canon.autonomy import Proposal
    events, proj, _ = wired
    prop = Proposal(proposing_agent="author", target_event_type="chapter.created",
                     target_aggregate_id="c1", payload={"title": "One", "prose": "p"})
    await events.append(EventType.PROPOSAL_CREATED, prop.id, prop)
    await proj.catch_up()
    cur = await proj._conn.execute("SELECT status, proposing_agent FROM proposals WHERE id=?", (prop.id,))
    row = await cur.fetchone()
    assert row == ("open", "author")


async def test_proposal_approved_flips_status(wired):
    from novelizer.canon.autonomy import Proposal, ProposalStatus
    events, proj, _ = wired
    prop = Proposal(proposing_agent="author", target_event_type="chapter.created",
                     target_aggregate_id="c1", payload={"title": "One", "prose": "p"})
    await events.append(EventType.PROPOSAL_CREATED, prop.id, prop)
    await proj.catch_up()
    approved = prop.model_copy(update={"status": ProposalStatus.approved})
    await events.append(EventType.PROPOSAL_APPROVED, prop.id, approved)
    await proj.catch_up()
    cur = await proj._conn.execute("SELECT status FROM proposals WHERE id=?", (prop.id,))
    assert (await cur.fetchone())[0] == "approved"


async def test_proposal_rejected_flips_status(wired):
    from novelizer.canon.autonomy import Proposal, ProposalStatus
    events, proj, _ = wired
    prop = Proposal(proposing_agent="editor", target_event_type="chapter.status_changed",
                     target_aggregate_id="c1", payload={})
    await events.append(EventType.PROPOSAL_CREATED, prop.id, prop)
    await proj.catch_up()
    rejected = prop.model_copy(update={"status": ProposalStatus.rejected})
    await events.append(EventType.PROPOSAL_REJECTED, prop.id, rejected)
    await proj.catch_up()
    cur = await proj._conn.execute("SELECT status FROM proposals WHERE id=?", (prop.id,))
    assert (await cur.fetchone())[0] == "rejected"


async def test_autonomy_changed_is_projected_singleton(wired):
    from novelizer.canon.autonomy import AutonomyState, AutonomyLevel
    events, proj, _ = wired
    st = AutonomyState(global_level=AutonomyLevel.gated_canon, overrides={"retconner": AutonomyLevel.gated_all})
    await events.append(EventType.AUTONOMY_CHANGED, "singleton", st)
    await proj.catch_up()
    cur = await proj._conn.execute("SELECT data FROM autonomy_state WHERE id='singleton'")
    row = await cur.fetchone()
    loaded = AutonomyState.model_validate_json(row[0])
    assert loaded.global_level == AutonomyLevel.gated_canon
    assert loaded.overrides["retconner"] == AutonomyLevel.gated_all


async def test_reset_state_clears_proposals_and_autonomy(wired):
    from novelizer.canon.autonomy import Proposal, AutonomyState, AutonomyLevel
    events, proj, _ = wired
    prop = Proposal(proposing_agent="author", target_event_type="chapter.created",
                     target_aggregate_id="c1", payload={})
    await events.append(EventType.PROPOSAL_CREATED, prop.id, prop)
    await events.append(EventType.AUTONOMY_CHANGED, "singleton", AutonomyState(global_level=AutonomyLevel.gated_all))
    await proj.catch_up()
    await proj._reset_state()
    cur = await proj._conn.execute("SELECT COUNT(*) FROM proposals")
    assert (await cur.fetchone())[0] == 0
    cur = await proj._conn.execute("SELECT COUNT(*) FROM autonomy_state")
    assert (await cur.fetchone())[0] == 0
