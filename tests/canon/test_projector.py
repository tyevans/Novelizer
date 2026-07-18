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


async def _thread_rows(proj):
    cur = await proj._conn.execute("SELECT data FROM threads ORDER BY rowid")
    return [json.loads(r[0]) for r in await cur.fetchall()]


async def test_thread_planted_is_projected(wired):
    from novelizer.canon.events import ThreadPlanted
    events, proj, _ = wired
    await events.append(EventType.THREAD_PLANTED, "the-locket",
                        ThreadPlanted(id="the-locket", name="The Locket"))
    await proj.catch_up()
    rows = await _thread_rows(proj)
    assert len(rows) == 1
    assert rows[0]["id"] == "the-locket" and rows[0]["state"] == "planted"


async def test_thread_touched_increments_count_and_updates_state(wired):
    from novelizer.canon.events import ThreadPlanted, ThreadTouched
    events, proj, _ = wired
    await events.append(EventType.THREAD_PLANTED, "the-locket", ThreadPlanted(id="the-locket", name="The Locket"))
    await events.append(EventType.THREAD_TOUCHED, "the-locket", ThreadTouched(id="the-locket", note="reappears"))
    await proj.catch_up()
    rows = await _thread_rows(proj)
    assert rows[0]["state"] == "touched"
    assert rows[0]["touch_count"] == 1
    assert rows[0]["last_note"] == "reappears"


async def test_thread_paid_off_is_terminal_and_absorbs_later_events(wired):
    from novelizer.canon.events import ThreadPlanted, ThreadTouched, ThreadPaidOff
    events, proj, _ = wired
    await events.append(EventType.THREAD_PLANTED, "the-locket", ThreadPlanted(id="the-locket", name="The Locket"))
    await events.append(EventType.THREAD_PAID_OFF, "the-locket", ThreadPaidOff(id="the-locket", note="resolved"))
    # A late touch after pay-off must be a no-op: the event lands in the log but the projection is unchanged.
    await events.append(EventType.THREAD_TOUCHED, "the-locket", ThreadTouched(id="the-locket", note="should not apply"))
    await proj.catch_up()
    rows = await _thread_rows(proj)
    assert rows[0]["state"] == "paid_off"
    assert rows[0]["touch_count"] == 0
    assert rows[0]["last_note"] == "resolved"
    log = await events.events_since(0)
    assert len(log) == 3  # the late touch is still a fact in the log


async def test_thread_abandoned_is_terminal(wired):
    from novelizer.canon.events import ThreadPlanted, ThreadAbandoned
    events, proj, _ = wired
    await events.append(EventType.THREAD_PLANTED, "the-locket", ThreadPlanted(id="the-locket", name="The Locket"))
    await events.append(EventType.THREAD_ABANDONED, "the-locket", ThreadAbandoned(id="the-locket"))
    await proj.catch_up()
    rows = await _thread_rows(proj)
    assert rows[0]["state"] == "abandoned"


async def test_reprojecting_thread_events_is_equivalent(wired):
    from novelizer.canon.events import ThreadPlanted, ThreadTouched, ThreadPaidOff
    events, proj, path = wired
    await events.append(EventType.THREAD_PLANTED, "the-locket", ThreadPlanted(id="the-locket", name="The Locket"))
    await events.append(EventType.THREAD_TOUCHED, "the-locket", ThreadTouched(id="the-locket"))
    await events.append(EventType.THREAD_PAID_OFF, "the-locket", ThreadPaidOff(id="the-locket"))
    await proj.catch_up()
    incremental = await _thread_rows(proj)
    proj2 = Projector(events, path)
    await proj2.init()
    await proj2._reset_state()
    await proj2.catch_up()
    from_scratch = await _thread_rows(proj2)
    await proj2.close()
    assert incremental == from_scratch


async def test_reset_state_clears_threads(wired):
    from novelizer.canon.events import ThreadPlanted
    events, proj, _ = wired
    await events.append(EventType.THREAD_PLANTED, "the-locket", ThreadPlanted(id="the-locket", name="The Locket"))
    await proj.catch_up()
    await proj._reset_state()
    cur = await proj._conn.execute("SELECT COUNT(*) FROM threads")
    assert (await cur.fetchone())[0] == 0


async def test_thread_replant_of_active_thread_does_not_reset_state(wired):
    from novelizer.canon.events import ThreadPlanted, ThreadTouched
    events, proj, _ = wired
    await events.append(EventType.THREAD_PLANTED, "the-locket", ThreadPlanted(id="the-locket", name="The Locket"))
    await events.append(EventType.THREAD_TOUCHED, "the-locket", ThreadTouched(id="the-locket", note="reappears"))
    # A re-plant of the same id must be a projection no-op: state/touch_count untouched.
    await events.append(EventType.THREAD_PLANTED, "the-locket", ThreadPlanted(id="the-locket", name="The Locket"))
    await proj.catch_up()
    rows = await _thread_rows(proj)
    assert len(rows) == 1
    assert rows[0]["state"] == "touched"
    assert rows[0]["touch_count"] == 1
    assert rows[0]["last_note"] == "reappears"
    log = await events.events_since(0)
    assert len(log) == 3  # the re-plant is still a fact in the log


async def test_thread_replant_of_paid_off_thread_does_not_resurrect(wired):
    from novelizer.canon.events import ThreadPlanted, ThreadPaidOff
    events, proj, _ = wired
    await events.append(EventType.THREAD_PLANTED, "the-locket", ThreadPlanted(id="the-locket", name="The Locket"))
    await events.append(EventType.THREAD_PAID_OFF, "the-locket", ThreadPaidOff(id="the-locket", note="resolved"))
    # Re-planting a paid-off thread must not resurrect it.
    await events.append(EventType.THREAD_PLANTED, "the-locket", ThreadPlanted(id="the-locket", name="The Locket"))
    await proj.catch_up()
    rows = await _thread_rows(proj)
    assert len(rows) == 1
    assert rows[0]["state"] == "paid_off"
    assert rows[0]["last_note"] == "resolved"


async def _structure_score_rows(proj):
    cur = await proj._conn.execute("SELECT data FROM structure_scores ORDER BY rowid")
    return [json.loads(r[0]) for r in await cur.fetchall()]


async def test_structure_scored_is_projected(wired):
    from novelizer.canon.events import AnnotationStructureScored
    events, proj, _ = wired
    await events.append(
        EventType.ANNOTATION_STRUCTURE_SCORED, "c1",
        AnnotationStructureScored(chapter_id="c1", tension=0.6, pacing_label="rising"),
    )
    await proj.catch_up()
    rows = await _structure_score_rows(proj)
    assert len(rows) == 1
    assert rows[0]["chapter_id"] == "c1" and rows[0]["tension"] == 0.6


async def test_structure_scored_replaces_prior_score_for_same_chapter(wired):
    from novelizer.canon.events import AnnotationStructureScored
    events, proj, _ = wired
    await events.append(EventType.ANNOTATION_STRUCTURE_SCORED, "c1",
                        AnnotationStructureScored(chapter_id="c1", tension=0.3, pacing_label="lull"))
    await events.append(EventType.ANNOTATION_STRUCTURE_SCORED, "c1",
                        AnnotationStructureScored(chapter_id="c1", tension=0.9, pacing_label="climax"))
    await proj.catch_up()
    rows = await _structure_score_rows(proj)
    assert len(rows) == 1
    assert rows[0]["tension"] == 0.9 and rows[0]["pacing_label"] == "climax"


async def test_reprojecting_structure_scores_is_equivalent(wired):
    from novelizer.canon.events import AnnotationStructureScored
    events, proj, path = wired
    await events.append(EventType.ANNOTATION_STRUCTURE_SCORED, "c1",
                        AnnotationStructureScored(chapter_id="c1", tension=0.6, pacing_label="rising"))
    await events.append(EventType.ANNOTATION_STRUCTURE_SCORED, "c2",
                        AnnotationStructureScored(chapter_id="c2", tension=0.2, pacing_label="lull"))
    await proj.catch_up()
    incremental = await _structure_score_rows(proj)
    proj2 = Projector(events, path)
    await proj2.init()
    await proj2._reset_state()
    await proj2.catch_up()
    from_scratch = await _structure_score_rows(proj2)
    await proj2.close()
    assert incremental == from_scratch


async def test_reset_state_clears_structure_scores(wired):
    from novelizer.canon.events import AnnotationStructureScored
    events, proj, _ = wired
    await events.append(EventType.ANNOTATION_STRUCTURE_SCORED, "c1",
                        AnnotationStructureScored(chapter_id="c1", tension=0.5, pacing_label="steady"))
    await proj.catch_up()
    await proj._reset_state()
    cur = await proj._conn.execute("SELECT COUNT(*) FROM structure_scores")
    assert (await cur.fetchone())[0] == 0


async def _secret_rows(proj):
    cur = await proj._conn.execute("SELECT data FROM secrets ORDER BY rowid")
    return [json.loads(r[0]) for r in await cur.fetchall()]


async def test_secret_created_is_projected(wired):
    from novelizer.canon.events import SecretCreated
    events, proj, _ = wired
    await events.append(EventType.SECRET_CREATED, "the-heir-lives",
                        SecretCreated(id="the-heir-lives", title="The Heir Lives"))
    await proj.catch_up()
    rows = await _secret_rows(proj)
    assert len(rows) == 1
    assert rows[0]["id"] == "the-heir-lives" and rows[0]["revealed"] is False


async def test_secret_created_is_first_creation_wins(wired):
    from novelizer.canon.events import SecretCreated
    events, proj, _ = wired
    await events.append(EventType.SECRET_CREATED, "the-heir-lives",
                        SecretCreated(id="the-heir-lives", title="The Heir Lives"))
    await events.append(EventType.SECRET_CREATED, "the-heir-lives",
                        SecretCreated(id="the-heir-lives", title="A Different Title"))
    await proj.catch_up()
    rows = await _secret_rows(proj)
    assert len(rows) == 1
    assert rows[0]["title"] == "The Heir Lives"


async def test_secret_learned_inserts_knowledge_row(wired):
    from novelizer.canon.events import SecretCreated, SecretLearned
    events, proj, _ = wired
    await events.append(EventType.SECRET_CREATED, "the-heir-lives", SecretCreated(id="the-heir-lives", title="The Heir Lives"))
    await events.append(EventType.SECRET_LEARNED, "the-heir-lives",
                        SecretLearned(id="the-heir-lives", character_id="mara", chapter_id="c2"))
    await proj.catch_up()
    cur = await proj._conn.execute("SELECT character_id FROM secret_knowledge WHERE secret_id=?", ("the-heir-lives",))
    assert [r[0] for r in await cur.fetchall()] == ["mara"]


async def test_secret_learned_twice_by_same_character_is_idempotent(wired):
    from novelizer.canon.events import SecretCreated, SecretLearned
    events, proj, _ = wired
    await events.append(EventType.SECRET_CREATED, "the-heir-lives", SecretCreated(id="the-heir-lives", title="The Heir Lives"))
    await events.append(EventType.SECRET_LEARNED, "the-heir-lives", SecretLearned(id="the-heir-lives", character_id="mara"))
    await events.append(EventType.SECRET_LEARNED, "the-heir-lives", SecretLearned(id="the-heir-lives", character_id="mara"))
    await proj.catch_up()
    cur = await proj._conn.execute("SELECT character_id FROM secret_knowledge WHERE secret_id=?", ("the-heir-lives",))
    assert [r[0] for r in await cur.fetchall()] == ["mara"]


async def test_secret_referenced_is_never_deduped(wired):
    from novelizer.canon.events import SecretCreated, SecretReferenced
    events, proj, _ = wired
    await events.append(EventType.SECRET_CREATED, "the-heir-lives", SecretCreated(id="the-heir-lives", title="The Heir Lives"))
    await events.append(EventType.SECRET_REFERENCED, "the-heir-lives",
                        SecretReferenced(id="the-heir-lives", character_id="mara", chapter_id="c3"))
    await events.append(EventType.SECRET_REFERENCED, "the-heir-lives",
                        SecretReferenced(id="the-heir-lives", character_id="mara", chapter_id="c3"))
    await proj.catch_up()
    cur = await proj._conn.execute("SELECT COUNT(*) FROM secret_references WHERE secret_id=?", ("the-heir-lives",))
    assert (await cur.fetchone())[0] == 2


async def test_secret_revealed_sets_flag_once(wired):
    from novelizer.canon.events import SecretCreated, SecretRevealed
    events, proj, _ = wired
    await events.append(EventType.SECRET_CREATED, "the-heir-lives", SecretCreated(id="the-heir-lives", title="The Heir Lives"))
    await events.append(EventType.SECRET_REVEALED, "the-heir-lives", SecretRevealed(id="the-heir-lives", note="told the crowd"))
    await events.append(EventType.SECRET_REVEALED, "the-heir-lives", SecretRevealed(id="the-heir-lives", note="told again"))
    await proj.catch_up()
    rows = await _secret_rows(proj)
    assert rows[0]["revealed"] is True


async def test_reprojecting_secret_events_is_equivalent(wired):
    from novelizer.canon.events import SecretCreated, SecretLearned, SecretRevealed
    events, proj, path = wired
    await events.append(EventType.SECRET_CREATED, "the-heir-lives", SecretCreated(id="the-heir-lives", title="The Heir Lives"))
    await events.append(EventType.SECRET_LEARNED, "the-heir-lives", SecretLearned(id="the-heir-lives", character_id="mara"))
    await events.append(EventType.SECRET_REVEALED, "the-heir-lives", SecretRevealed(id="the-heir-lives"))
    await proj.catch_up()
    incremental = await _secret_rows(proj)
    proj2 = Projector(events, path)
    await proj2.init()
    await proj2._reset_state()
    await proj2.catch_up()
    from_scratch = await _secret_rows(proj2)
    await proj2.close()
    assert incremental == from_scratch


async def test_reset_state_clears_secret_tables(wired):
    from novelizer.canon.events import SecretCreated, SecretLearned, SecretReferenced
    events, proj, _ = wired
    await events.append(EventType.SECRET_CREATED, "the-heir-lives", SecretCreated(id="the-heir-lives", title="The Heir Lives"))
    await events.append(EventType.SECRET_LEARNED, "the-heir-lives", SecretLearned(id="the-heir-lives", character_id="mara"))
    await events.append(EventType.SECRET_REFERENCED, "the-heir-lives", SecretReferenced(id="the-heir-lives", character_id="mara"))
    await proj.catch_up()
    await proj._reset_state()
    for table in ("secrets", "secret_knowledge", "secret_references"):
        cur = await proj._conn.execute(f"SELECT COUNT(*) FROM {table}")
        assert (await cur.fetchone())[0] == 0


async def _causal_edge_rows(proj):
    cur = await proj._conn.execute(
        "SELECT cause_chapter_id, effect_chapter_id, note FROM causal_edges ORDER BY rowid"
    )
    return await cur.fetchall()


async def test_causal_edge_declared_is_projected(wired):
    from novelizer.canon.events import CausalEdgeDeclared
    events, proj, _ = wired
    await events.append(EventType.CAUSAL_EDGE_DECLARED, "c3",
                        CausalEdgeDeclared(cause_chapter_id="c1", effect_chapter_id="c3", note="fire forces the move"))
    await proj.catch_up()
    rows = await _causal_edge_rows(proj)
    assert rows == [("c1", "c3", "fire forces the move")]


async def test_causal_edge_declared_is_never_deduped(wired):
    from novelizer.canon.events import CausalEdgeDeclared
    events, proj, _ = wired
    edge = CausalEdgeDeclared(cause_chapter_id="c1", effect_chapter_id="c3")
    await events.append(EventType.CAUSAL_EDGE_DECLARED, "c3", edge)
    await events.append(EventType.CAUSAL_EDGE_DECLARED, "c3", edge)
    await proj.catch_up()
    rows = await _causal_edge_rows(proj)
    assert len(rows) == 2


async def test_reprojecting_causal_edges_is_equivalent(wired):
    from novelizer.canon.events import CausalEdgeDeclared
    events, proj, path = wired
    await events.append(EventType.CAUSAL_EDGE_DECLARED, "c2", CausalEdgeDeclared(cause_chapter_id="c1", effect_chapter_id="c2"))
    await events.append(EventType.CAUSAL_EDGE_DECLARED, "c3", CausalEdgeDeclared(cause_chapter_id="c2", effect_chapter_id="c3"))
    await proj.catch_up()
    incremental = await _causal_edge_rows(proj)
    proj2 = Projector(events, path)
    await proj2.init()
    await proj2._reset_state()
    await proj2.catch_up()
    from_scratch = await _causal_edge_rows(proj2)
    await proj2.close()
    assert incremental == from_scratch


async def test_reset_state_clears_causal_edges(wired):
    from novelizer.canon.events import CausalEdgeDeclared
    events, proj, _ = wired
    await events.append(EventType.CAUSAL_EDGE_DECLARED, "c2", CausalEdgeDeclared(cause_chapter_id="c1", effect_chapter_id="c2"))
    await proj.catch_up()
    await proj._reset_state()
    cur = await proj._conn.execute("SELECT COUNT(*) FROM causal_edges")
    assert (await cur.fetchone())[0] == 0
