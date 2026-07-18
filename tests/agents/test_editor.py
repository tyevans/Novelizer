import os
import tempfile
import pytest
from novelizer.canon.event_store import EventStore
from novelizer.canon.projector import Projector
from novelizer.canon.read_store import ReadStore
from novelizer.canon.committer import Committer
from novelizer.canon.events import EventType
from novelizer.agents.editor import Editor
from novelizer.agents.schemas import EditorVerdict
from novelizer.store.models import Chapter, EditorialStatus, Character


class FakeRunner:
    def __init__(self, out): self._out = out; self.calls = []
    async def ainvoke(self, inputs):
        self.calls.append(inputs)
        return {"structured_response": self._out}


@pytest.fixture
async def stack():
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    events = EventStore(path); await events.init()
    proj = Projector(events, path); await proj.init()
    read = ReadStore(path); await read.init()
    yield events, proj, read, Committer(events)
    await read.close(); await proj.close(); await events.close(); os.unlink(path)


async def test_readiness_scales_with_drafts(stack):
    events, proj, read, committer = stack
    for i in range(3):
        await events.append(EventType.CHAPTER_CREATED, f"c{i}", Chapter(id=f"c{i}", title=str(i), prose="p"))
    await proj.catch_up()
    assert await Editor(FakeRunner(None), read, committer).readiness() == 1.0


async def test_approve_promotes_to_reviewed(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
    await proj.catch_up()
    agent = Editor(FakeRunner(EditorVerdict(verdict="approve", notes="clean")), read, committer)
    await agent.run_once()
    await proj.catch_up()
    ch = await read.get_chapter("c1")
    assert ch.editorial_status == EditorialStatus.reviewed and ch.editor_notes == "clean"


async def test_revise_keeps_draft_and_notes_author(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
    await proj.catch_up()
    agent = Editor(FakeRunner(EditorVerdict(verdict="revise", notes="middle sags")), read, committer)
    await agent.run_once()
    await proj.catch_up()
    assert (await read.get_chapter("c1")).editorial_status == EditorialStatus.draft
    notes = await read.list_unconsumed_signals(target_agent="author")
    assert any("middle sags" in s.body for s in notes)


async def test_editor_prompt_includes_active_prose_profile(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
    await proj.catch_up()

    class RecordingRunner:
        def __init__(self, out):
            self._out = out
            self.calls = []

        async def ainvoke(self, inputs):
            self.calls.append(inputs)
            return {"structured_response": self._out}

    runner = RecordingRunner(EditorVerdict(verdict="approve", notes="clean"))
    agent = Editor(runner, read, committer, casting_note="Spare, concrete, unadorned.")
    ctx = await agent.poll()
    await agent.work(ctx)
    sent = runner.calls[-1]["messages"][0]["content"]
    assert "Spare, concrete, unadorned." in sent
    assert "Enforce this prose voice:" in sent


async def test_editor_prompt_includes_personality_when_set(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
    await proj.catch_up()
    runner = FakeRunner(EditorVerdict(verdict="approve", notes="clean"))
    agent = Editor(runner, read, committer, personality="A precise, unsentimental line editor.")
    ctx = await agent.poll()
    await agent.work(ctx)
    sent = runner.calls[-1]["messages"][0]["content"]
    assert "A precise, unsentimental line editor." in sent
    assert "In character:" in sent


async def test_editor_commit_emits_remark_on_approval(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
    await proj.catch_up()
    verdict = EditorVerdict(verdict="approve", notes="clean", feed_note="Finally, a clean draft.")
    agent = Editor(FakeRunner(verdict), read, committer)
    await agent.run_once()
    await proj.catch_up()
    log = await events.events_since(0)
    remarks = [e for e in log if e.event_type == EventType.AGENT_REMARKED]
    assert len(remarks) == 1
    assert remarks[0].payload["note"] == "Finally, a clean draft."


async def test_editor_commit_emits_remark_on_revision(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
    await proj.catch_up()
    verdict = EditorVerdict(verdict="revise", notes="middle sags", feed_note="This needs more tension.")
    agent = Editor(FakeRunner(verdict), read, committer)
    await agent.run_once()
    await proj.catch_up()
    log = await events.events_since(0)
    remarks = [e for e in log if e.event_type == EventType.AGENT_REMARKED]
    assert len(remarks) == 1
    assert remarks[0].payload["note"] == "This needs more tension."


async def test_editor_prompt_includes_character_voices_when_present(stack):
    events, proj, read, committer = stack
    await events.append(
        EventType.CHARACTER_CREATED, "ch1",
        Character(id="ch1", name="Mira", voice="Speaks in short, clipped sentences; never says 'I love you' outright."),
    )
    await events.append(
        EventType.CHAPTER_CREATED, "c1",
        Chapter(id="c1", title="One", prose="p", character_ids=["ch1"]),
    )
    await proj.catch_up()
    runner = FakeRunner(EditorVerdict(verdict="approve", notes="clean"))
    agent = Editor(runner, read, committer)
    ctx = await agent.poll()
    await agent.work(ctx)
    sent = runner.calls[-1]["messages"][0]["content"]
    assert "Mira" in sent
    assert "Speaks in short, clipped sentences" in sent
    assert "Character voices:" in sent


async def test_editor_prompt_omits_voices_section_when_none_set(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHARACTER_CREATED, "ch1", Character(id="ch1", name="Mira"))
    await events.append(
        EventType.CHAPTER_CREATED, "c1",
        Chapter(id="c1", title="One", prose="p", character_ids=["ch1"]),
    )
    await proj.catch_up()
    runner = FakeRunner(EditorVerdict(verdict="approve", notes="clean"))
    agent = Editor(runner, read, committer)
    ctx = await agent.poll()
    await agent.work(ctx)
    sent = runner.calls[-1]["messages"][0]["content"]
    assert "Character voices:" not in sent
    assert sent == f"Chapter title: One\n\nProse:\np"


from novelizer.agents.schemas import ThreadIntent
from novelizer.canon.events import ThreadPlanted


async def test_editor_commit_touches_a_known_active_thread(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
    await events.append(EventType.THREAD_PLANTED, "the-locket", ThreadPlanted(id="the-locket", name="The Locket"))
    await proj.catch_up()
    verdict = EditorVerdict(
        verdict="approve", notes="clean",
        thread_intents=[ThreadIntent(action="touch", id="the-locket", note="resurfaces")],
    )
    agent = Editor(FakeRunner(verdict), read, committer)
    await agent.run_once()
    await proj.catch_up()
    thread = await read.get_thread("the-locket")
    assert thread.touch_count == 1
    assert thread.last_chapter_id == "c1"


async def test_editor_commit_drops_pay_off_for_unknown_thread_id(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
    await proj.catch_up()
    verdict = EditorVerdict(verdict="approve", notes="clean", thread_intents=[ThreadIntent(action="pay_off", id="ghost")])
    agent = Editor(FakeRunner(verdict), read, committer)
    await agent.run_once()
    await proj.catch_up()
    log = await events.events_since(0)
    assert [e.event_type for e in log if e.event_type.startswith("thread.")] == []


async def test_editor_commit_with_no_thread_intents_emits_no_thread_events(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
    await proj.catch_up()
    verdict = EditorVerdict(verdict="approve", notes="clean")
    agent = Editor(FakeRunner(verdict), read, committer)
    await agent.run_once()
    await proj.catch_up()
    log = await events.events_since(0)
    assert [e.event_type for e in log if e.event_type.startswith("thread.")] == []
