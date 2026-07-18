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
