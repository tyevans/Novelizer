import os
import tempfile
import pytest
from novelizer.canon.event_store import EventStore
from novelizer.canon.projector import Projector
from novelizer.canon.read_store import ReadStore
from novelizer.canon.committer import Committer
from novelizer.canon.events import EventType, ThreadPlanted
from novelizer.agents.author import Author, ChapterDraft
from novelizer.agents.schemas import ThreadIntent
from novelizer.store.models import Chapter, DirectorSignal, SignalKind


class FakeRunner:
    def __init__(self, draft): self._draft = draft; self.calls = []
    async def ainvoke(self, inputs):
        self.calls.append(inputs)
        return {"structured_response": self._draft}


@pytest.fixture
async def stack():
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    events = EventStore(path); await events.init()
    proj = Projector(events, path); await proj.init()
    read = ReadStore(path); await read.init()
    yield events, proj, read, Committer(events)
    await read.close(); await proj.close(); await events.close(); os.unlink(path)


async def test_readiness_drops_with_draft_backlog(stack):
    events, proj, read, committer = stack
    for i in range(3):
        await events.append(EventType.CHAPTER_CREATED, f"c{i}", Chapter(id=f"c{i}", title=str(i), prose="p"))
    await proj.catch_up()
    author = Author(FakeRunner(None), read, committer)
    assert await author.readiness() == 0.0


async def test_run_once_appends_and_projects_a_chapter(stack):
    events, proj, read, committer = stack
    draft = ChapterDraft(title="The Salt Road", prose="The road held its salt like a grudge.")
    author = Author(FakeRunner(draft), read, committer)
    await author.run_once()
    await proj.catch_up()
    assert "The Salt Road" in [c.title for c in await read.list_chapters()]


async def test_run_once_consumes_targeted_signals(stack):
    events, proj, read, committer = stack
    await events.append(EventType.DIRECTOR_SIGNAL_CREATED, "s1",
                        DirectorSignal(id="s1", kind=SignalKind.seed, body="a storm is coming"))
    await proj.catch_up()
    author = Author(FakeRunner(ChapterDraft(title="T", prose="P")), read, committer)
    await author.run_once()
    await proj.catch_up()
    assert await read.list_unconsumed_signals(target_agent="author") == []


async def test_work_returns_none_is_noop(stack):
    events, proj, read, committer = stack
    author = Author(FakeRunner(None), read, committer)
    await author.run_once()
    await proj.catch_up()
    assert await read.list_chapters() == []


async def test_work_prompt_includes_casting_note_when_set(stack):
    events, proj, read, committer = stack
    draft = ChapterDraft(title="T", prose="P")
    runner = FakeRunner(draft)
    author = Author(runner, read, committer, casting_note="Spare, concrete, unadorned.")
    ctx = await author.poll()
    await author.work(ctx)
    sent = runner.calls[-1]["messages"][0]["content"]
    assert "Spare, concrete, unadorned." in sent
    assert "Write in this prose voice:" in sent


async def test_work_prompt_omits_casting_note_when_unset(stack):
    events, proj, read, committer = stack
    draft = ChapterDraft(title="T", prose="P")
    runner = FakeRunner(draft)
    author = Author(runner, read, committer)
    ctx = await author.poll()
    await author.work(ctx)
    sent = runner.calls[-1]["messages"][0]["content"]
    assert "Write in this prose voice:" not in sent


async def test_two_profiles_yield_different_prompts(stack):
    events, proj, read, committer = stack
    draft = ChapterDraft(title="T", prose="P")
    sparse_runner = FakeRunner(draft)
    lush_runner = FakeRunner(draft)
    sparse_author = Author(sparse_runner, read, committer, casting_note="Spare, concrete, unadorned.")
    lush_author = Author(lush_runner, read, committer, casting_note="Ornate, sensory, gothic.")
    ctx = await sparse_author.poll()
    await sparse_author.work(ctx)
    await lush_author.work(ctx)
    sparse_prompt = sparse_runner.calls[-1]["messages"][0]["content"]
    lush_prompt = lush_runner.calls[-1]["messages"][0]["content"]
    assert sparse_prompt != lush_prompt
    assert "Spare, concrete, unadorned." in sparse_prompt
    assert "Ornate, sensory, gothic." in lush_prompt


async def test_work_prompt_includes_personality_when_set(stack):
    events, proj, read, committer = stack
    draft = ChapterDraft(title="T", prose="P")
    runner = FakeRunner(draft)
    author = Author(runner, read, committer, personality="A restless, romantic chronicler.")
    ctx = await author.poll()
    await author.work(ctx)
    sent = runner.calls[-1]["messages"][0]["content"]
    assert "A restless, romantic chronicler." in sent
    assert "In character:" in sent


async def test_work_prompt_omits_personality_line_when_unset(stack):
    events, proj, read, committer = stack
    draft = ChapterDraft(title="T", prose="P")
    runner = FakeRunner(draft)
    author = Author(runner, read, committer)
    ctx = await author.poll()
    await author.work(ctx)
    sent = runner.calls[-1]["messages"][0]["content"]
    assert "In character:" not in sent


async def test_commit_emits_agent_remarked_when_feed_note_present(stack):
    events, proj, read, committer = stack
    draft = ChapterDraft(title="T", prose="P", feed_note="Another chapter, another heartbreak.")
    author = Author(FakeRunner(draft), read, committer)
    await author.run_once()
    await proj.catch_up()
    log = await events.events_since(0)
    remarks = [e for e in log if e.event_type == EventType.AGENT_REMARKED]
    assert len(remarks) == 1
    assert remarks[0].payload["agent_name"] == "author"
    assert remarks[0].payload["note"] == "Another chapter, another heartbreak."


async def test_commit_emits_no_remark_when_feed_note_empty(stack):
    events, proj, read, committer = stack
    draft = ChapterDraft(title="T", prose="P")
    author = Author(FakeRunner(draft), read, committer)
    await author.run_once()
    await proj.catch_up()
    log = await events.events_since(0)
    assert [e for e in log if e.event_type == EventType.AGENT_REMARKED] == []


async def test_author_commit_plants_a_thread_from_structured_output(stack):
    events, proj, read, committer = stack
    draft = ChapterDraft(
        title="T", prose="P",
        thread_intents=[ThreadIntent(action="plant", name="The Locket")],
    )
    author = Author(FakeRunner(draft), read, committer)
    await author.run_once()
    await proj.catch_up()
    thread = await read.get_thread("the-locket")
    assert thread is not None and thread.name == "The Locket"


async def test_author_commit_touches_a_known_active_thread(stack):
    events, proj, read, committer = stack
    await events.append(EventType.THREAD_PLANTED, "the-locket", ThreadPlanted(id="the-locket", name="The Locket"))
    await proj.catch_up()
    draft = ChapterDraft(
        title="T", prose="P",
        thread_intents=[ThreadIntent(action="touch", id="the-locket", note="reappears")],
    )
    author = Author(FakeRunner(draft), read, committer)
    await author.run_once()
    await proj.catch_up()
    thread = await read.get_thread("the-locket")
    assert thread.touch_count == 1


async def test_author_commit_drops_touch_for_unknown_thread_id(stack):
    events, proj, read, committer = stack
    draft = ChapterDraft(
        title="T", prose="P",
        thread_intents=[ThreadIntent(action="touch", id="ghost-thread")],
    )
    author = Author(FakeRunner(draft), read, committer)
    await author.run_once()
    await proj.catch_up()
    log = await events.events_since(0)
    assert [e.event_type for e in log if e.event_type.startswith("thread.")] == []


async def test_author_commit_with_no_thread_intents_emits_no_thread_events(stack):
    events, proj, read, committer = stack
    draft = ChapterDraft(title="T", prose="P")
    author = Author(FakeRunner(draft), read, committer)
    await author.run_once()
    await proj.catch_up()
    log = await events.events_since(0)
    assert [e.event_type for e in log if e.event_type.startswith("thread.")] == []
