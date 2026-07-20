"""The Editor -> Author revision loop has to terminate.

A revise signal sends the chapter back to the Author, whose re-draft commits
chapter.revised, which the projector puts back into `draft` -- straight back
into the Editor's poll. Nothing counted the trips, so a chapter the Editor
keeps disliking can circle forever, and unconstrained revision flattens prose
besides.

See docs/agent-prompting/proposal-editor.md §4.
"""
from __future__ import annotations

import os
import tempfile

import pytest

from novelizer.canon.committer import Committer
from novelizer.canon.event_store import EventStore
from novelizer.canon.events import ChapterRevised, EventType
from novelizer.canon.projector import Projector
from novelizer.canon.read_store import ReadStore
from novelizer.store.models import Chapter


@pytest.fixture
async def stack():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    events = EventStore(path)
    await events.init()
    proj = Projector(events, path)
    await proj.init()
    read = ReadStore(path)
    await read.init()
    yield events, proj, read, Committer(events)
    await read.close()
    await proj.close()
    await events.close()
    os.unlink(path)


class TestRevisionCountProjection:
    async def test_new_chapter_starts_at_zero(self, stack):
        events, proj, read, _ = stack
        await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="T", prose="p"))
        await proj.catch_up()
        assert (await read.get_chapter("c1")).revision_count == 0

    async def test_each_revision_increments_the_count(self, stack):
        events, proj, read, _ = stack
        await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="T", prose="p"))
        await proj.catch_up()
        for i in range(3):
            await events.append(
                EventType.CHAPTER_REVISED, "c1",
                ChapterRevised(chapter_id="c1", prose=f"v{i}", editor_notes_ref="s1"),
            )
            await proj.catch_up()
            assert (await read.get_chapter("c1")).revision_count == i + 1

    async def test_count_is_derived_by_replay_not_stored_state(self, stack):
        """Replaying the same log into an empty projection must reproduce the
        count exactly -- otherwise it is state, not a projection."""
        events, proj, read, _ = stack
        await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="T", prose="p"))
        for _ in range(2):
            await events.append(
                EventType.CHAPTER_REVISED, "c1",
                ChapterRevised(chapter_id="c1", prose="v", editor_notes_ref="s1"),
            )
        await proj.catch_up()

        fd, path2 = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        replay_proj = Projector(events, path2)
        await replay_proj.init()
        replay_read = ReadStore(path2)
        await replay_read.init()
        try:
            await replay_proj.catch_up()
            assert (await replay_read.get_chapter("c1")).revision_count == 2
        finally:
            await replay_read.close()
            await replay_proj.close()
            os.unlink(path2)

    def test_chapters_written_before_the_field_default_to_zero(self):
        assert Chapter.model_validate({"title": "T", "prose": "p"}).revision_count == 0


class TestEditorRespectsTheBudget:
    async def test_editor_approves_a_chapter_past_the_revision_cap(self, stack):
        """Past the cap the verdict is forced: the Editor may still dislike it,
        but the loop has to end somewhere and shipping beats circling."""
        from novelizer.agents.editor import MAX_REVISIONS, Editor
        from novelizer.agents.schemas import EditorVerdict

        events, proj, read, committer = stack
        ch = Chapter(id="c1", title="T", prose="p")
        await events.append(EventType.CHAPTER_CREATED, "c1", ch)
        for _ in range(MAX_REVISIONS):
            await events.append(
                EventType.CHAPTER_REVISED, "c1",
                ChapterRevised(chapter_id="c1", prose="v", editor_notes_ref="s1"),
            )
        await proj.catch_up()

        class FakeRunner:
            def __init__(self):
                self.calls = []

            async def ainvoke(self, inputs):
                self.calls.append(inputs)
                return {"structured_response": EditorVerdict(verdict="revise", notes="still not right")}

        runner = FakeRunner()
        editor = Editor(runner, read, committer)
        ctx = await editor.poll()
        verdict = await editor.work(ctx)
        await editor.commit(verdict, ctx)
        await proj.catch_up()

        signals = await read.list_unconsumed_signals(target_agent="author")
        assert [s for s in signals if s.target_entity == "c1"] == []

    async def test_context_tells_the_editor_how_many_revisions_have_happened(self, stack):
        """The prompt asks it to weigh prior revisions; it can only do that if
        the count is in front of it."""
        from novelizer.agents.editor import Editor
        from novelizer.agents.schemas import EditorVerdict

        events, proj, read, committer = stack
        await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="T", prose="p"))
        await events.append(
            EventType.CHAPTER_REVISED, "c1",
            ChapterRevised(chapter_id="c1", prose="v", editor_notes_ref="s1"),
        )
        await proj.catch_up()

        class FakeRunner:
            def __init__(self):
                self.calls = []

            async def ainvoke(self, inputs):
                self.calls.append(inputs)
                return {"structured_response": EditorVerdict(verdict="approve", notes="fine")}

        runner = FakeRunner()
        editor = Editor(runner, read, committer)
        ctx = await editor.poll()
        await editor.work(ctx)
        sent = runner.calls[-1]["messages"][0]["content"]
        assert "revised 1 time" in sent
