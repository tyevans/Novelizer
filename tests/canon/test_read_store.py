import os
import tempfile
import pytest
from novelizer.canon.event_store import EventStore
from novelizer.canon.projector import Projector
from novelizer.canon.read_store import ReadStore
from novelizer.canon.events import EventType
from novelizer.canon.autonomy import Proposal, AutonomyState, AutonomyLevel
from novelizer.store.models import Chapter, WorldEntry, Character, DirectorSignal, SignalKind


@pytest.fixture
async def stack():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    events = EventStore(path); await events.init()
    proj = Projector(events, path); await proj.init()
    read = ReadStore(path); await read.init()
    yield events, proj, read
    await read.close(); await proj.close(); await events.close()
    os.unlink(path)


async def test_chapter_visible_after_projection(stack):
    events, proj, read = stack
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
    await proj.catch_up()
    chapters = await read.list_chapters()
    assert [c.title for c in chapters] == ["One"]
    assert (await read.get_chapter("c1")).prose == "p"


async def test_unconsumed_signals_filtered_by_target(stack):
    events, proj, read = stack
    await events.append(EventType.DIRECTOR_SIGNAL_CREATED, "s1",
                        DirectorSignal(id="s1", kind=SignalKind.seed, body="broadcast"))
    await events.append(EventType.DIRECTOR_SIGNAL_CREATED, "s2",
                        DirectorSignal(id="s2", kind=SignalKind.focus, body="for-editor", target_agent="editor"))
    await proj.catch_up()
    for_author = await read.list_unconsumed_signals(target_agent="author")
    assert {s.id for s in for_author} == {"s1"}  # broadcast only, not editor-targeted


async def test_unconsumed_signals_survive_unknown_kind(stack):
    """Regression: Scheduler.tick calls list_unconsumed_signals every cycle;
    one persisted signal whose kind this model version does not know must not
    raise (live incident: kind='revise' events wedged every tick for readers
    whose SignalKind predated the member)."""
    events, proj, read = stack
    await events.append(EventType.DIRECTOR_SIGNAL_CREATED, "s1",
                        DirectorSignal(id="s1", kind=SignalKind.seed, body="broadcast"))
    newer_writer_payload = DirectorSignal(
        id="s2", kind=SignalKind.focus, body="from the future", target_agent="author"
    ).model_dump(mode="json")
    newer_writer_payload["kind"] = "tempo"  # a kind this reader has never heard of
    await events.append_raw(EventType.DIRECTOR_SIGNAL_CREATED, "s2", newer_writer_payload)
    await proj.catch_up()
    sigs = await read.list_unconsumed_signals()
    assert {s.id for s in sigs} == {"s1", "s2"}
    assert next(s.kind for s in sigs if s.id == "s2") == "tempo"


async def test_consumed_signal_disappears(stack):
    events, proj, read = stack
    sig = DirectorSignal(id="s1", kind=SignalKind.seed, body="x")
    await events.append(EventType.DIRECTOR_SIGNAL_CREATED, "s1", sig)
    await events.append(EventType.DIRECTOR_SIGNAL_CONSUMED, "s1", sig)
    await proj.catch_up()
    assert await read.list_unconsumed_signals() == []


async def test_list_and_get_proposals(stack):
    events, proj, read = stack
    prop = Proposal(proposing_agent="author", target_event_type="chapter.created",
                     target_aggregate_id="c1", payload={"title": "One"})
    await events.append(EventType.PROPOSAL_CREATED, prop.id, prop)
    await proj.catch_up()
    open_props = await read.list_proposals(status="open")
    assert len(open_props) == 1 and open_props[0].proposing_agent == "author"
    fetched = await read.get_proposal(prop.id)
    assert fetched is not None and fetched.target_aggregate_id == "c1"
    assert await read.get_proposal("missing") is None


async def test_get_autonomy_state_defaults_to_full_auto(stack):
    _, _, read = stack
    st = await read.get_autonomy_state()
    assert st.global_level == AutonomyLevel.full_auto
    assert st.overrides == {}


async def test_get_autonomy_state_reflects_latest_change(stack):
    events, proj, read = stack
    await events.append(
        EventType.AUTONOMY_CHANGED, "singleton",
        AutonomyState(global_level=AutonomyLevel.gated_all, overrides={"author": AutonomyLevel.full_auto}),
    )
    await proj.catch_up()
    st = await read.get_autonomy_state()
    assert st.global_level == AutonomyLevel.gated_all
    assert st.overrides["author"] == AutonomyLevel.full_auto


async def test_list_and_get_threads(stack):
    from novelizer.canon.events import ThreadPlanted, ThreadTouched
    events, proj, read = stack
    await events.append(EventType.THREAD_PLANTED, "the-locket", ThreadPlanted(id="the-locket", name="The Locket"))
    await events.append(EventType.THREAD_PLANTED, "mira-revenge", ThreadPlanted(id="mira-revenge", name="Mira's Revenge"))
    await events.append(EventType.THREAD_TOUCHED, "the-locket", ThreadTouched(id="the-locket", note="reappears"))
    await proj.catch_up()
    threads = await read.list_threads()
    assert {t.id for t in threads} == {"the-locket", "mira-revenge"}
    fetched = await read.get_thread("the-locket")
    assert fetched is not None and fetched.touch_count == 1
    assert await read.get_thread("missing") is None


async def test_list_and_get_themes(stack):
    from novelizer.canon.events import ThemeIntroduced, ThemeDeveloped
    events, proj, read = stack
    await events.append(EventType.THEME_INTRODUCED, "isolation", ThemeIntroduced(id="isolation", title="Isolation"))
    await events.append(EventType.THEME_INTRODUCED, "memory", ThemeIntroduced(id="memory", title="Memory"))
    await events.append(EventType.THEME_DEVELOPED, "isolation", ThemeDeveloped(id="isolation", note="deepens"))
    await proj.catch_up()
    themes = await read.list_themes()
    assert {t.id for t in themes} == {"isolation", "memory"}
    fetched = await read.get_theme("isolation")
    assert fetched is not None and fetched.touch_count == 1
    assert await read.get_theme("missing") is None


async def test_list_and_get_structure_scores(stack):
    from novelizer.canon.events import AnnotationStructureScored
    events, proj, read = stack
    await events.append(EventType.ANNOTATION_STRUCTURE_SCORED, "c1",
                        AnnotationStructureScored(chapter_id="c1", tension=0.6, pacing_label="rising"))
    await events.append(EventType.ANNOTATION_STRUCTURE_SCORED, "c2",
                        AnnotationStructureScored(chapter_id="c2", tension=0.2, pacing_label="lull"))
    await proj.catch_up()
    scores = await read.list_structure_scores()
    assert {s.chapter_id for s in scores} == {"c1", "c2"}
    fetched = await read.get_structure_score("c1")
    assert fetched is not None and fetched.tension == 0.6
    assert await read.get_structure_score("missing") is None


async def test_list_and_get_secrets(stack):
    from novelizer.canon.events import SecretCreated
    events, proj, read = stack
    await events.append(EventType.SECRET_CREATED, "the-heir-lives", SecretCreated(id="the-heir-lives", title="The Heir Lives"))
    await events.append(EventType.SECRET_CREATED, "the-map-is-forged", SecretCreated(id="the-map-is-forged", title="The Map Is Forged"))
    await proj.catch_up()
    secrets = await read.list_secrets()
    assert {s.id for s in secrets} == {"the-heir-lives", "the-map-is-forged"}
    fetched = await read.get_secret("the-heir-lives")
    assert fetched is not None and fetched.title == "The Heir Lives"
    assert await read.get_secret("missing") is None


async def test_knowledge_matrix_reflects_learned_and_revealed(stack):
    from novelizer.canon.events import SecretCreated, SecretLearned, SecretRevealed
    events, proj, read = stack
    await events.append(EventType.SECRET_CREATED, "the-heir-lives", SecretCreated(id="the-heir-lives", title="The Heir Lives"))
    await events.append(EventType.SECRET_CREATED, "the-map-is-forged", SecretCreated(id="the-map-is-forged", title="The Map Is Forged"))
    await events.append(EventType.SECRET_LEARNED, "the-heir-lives", SecretLearned(id="the-heir-lives", character_id="mara"))
    await events.append(EventType.SECRET_REVEALED, "the-map-is-forged", SecretRevealed(id="the-map-is-forged"))
    await proj.catch_up()
    matrix = await read.knowledge_matrix()
    assert matrix["the-heir-lives"] == {"revealed": False, "known_by": {"mara"}}
    assert matrix["the-map-is-forged"] == {"revealed": True, "known_by": set()}


async def test_list_causal_edges(stack):
    from novelizer.canon.events import CausalEdgeDeclared
    events, proj, read = stack
    await events.append(EventType.CAUSAL_EDGE_DECLARED, "c2", CausalEdgeDeclared(cause_chapter_id="c1", effect_chapter_id="c2"))
    await events.append(EventType.CAUSAL_EDGE_DECLARED, "c3", CausalEdgeDeclared(cause_chapter_id="c2", effect_chapter_id="c3", note="the letter arrives"))
    await proj.catch_up()
    edges = await read.list_causal_edges()
    assert [(e.cause_chapter_id, e.effect_chapter_id, e.note) for e in edges] == [
        ("c1", "c2", ""), ("c2", "c3", "the letter arrives"),
    ]


async def test_list_secret_references_filters_by_secret_id(stack):
    from novelizer.canon.events import SecretCreated, SecretReferenced
    events, proj, read = stack
    await events.append(EventType.SECRET_CREATED, "the-heir-lives", SecretCreated(id="the-heir-lives", title="The Heir Lives"))
    await events.append(EventType.SECRET_REFERENCED, "the-heir-lives", SecretReferenced(id="the-heir-lives", character_id="mara", chapter_id="c3"))
    await events.append(EventType.SECRET_REFERENCED, "the-heir-lives", SecretReferenced(id="the-heir-lives", character_id="ren", chapter_id="c4"))
    await proj.catch_up()
    all_refs = await read.list_secret_references()
    assert len(all_refs) == 2
    filtered = await read.list_secret_references(secret_id="the-heir-lives")
    assert {r.character_id for r in filtered} == {"mara", "ren"}
    assert await read.list_secret_references(secret_id="missing") == []
