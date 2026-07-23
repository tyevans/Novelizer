import os
import tempfile
import pytest
from novelizer.canon.event_store import EventStore
from novelizer.canon.projector import Projector
from novelizer.canon.read_store import ReadStore
from novelizer.canon.committer import Committer
from novelizer.canon.events import EventType, AnnotationStructureScored
from novelizer.agents.structure_analyst import StructureAnalyst
from novelizer.agents.schemas import ChapterScore, StructureAnalystOutput
from novelizer.store.models import Chapter


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


async def test_readiness_is_zero_with_no_unscored_chapters(stack):
    events, proj, read, committer = stack
    analyst = StructureAnalyst(FakeRunner(None), read, committer)
    assert await analyst.readiness() == 0.0


async def test_readiness_is_proportional_to_unscored_chapter_count(stack):
    events, proj, read, committer = stack
    for i in range(2):
        await events.append(EventType.CHAPTER_CREATED, f"c{i}", Chapter(id=f"c{i}", title=str(i), prose="p"))
    await proj.catch_up()
    analyst = StructureAnalyst(FakeRunner(None), read, committer)
    assert await analyst.readiness() == pytest.approx(2 / 3)


async def test_readiness_excludes_already_scored_chapters(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
    await events.append(EventType.ANNOTATION_STRUCTURE_SCORED, "c1",
                        AnnotationStructureScored(chapter_id="c1", tension=0.5, pacing_label="steady"))
    await proj.catch_up()
    analyst = StructureAnalyst(FakeRunner(None), read, committer)
    assert await analyst.readiness() == 0.0


async def test_run_once_emits_a_structure_scored_event_per_chapter(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
    await events.append(EventType.CHAPTER_CREATED, "c2", Chapter(id="c2", title="Two", prose="p"))
    await proj.catch_up()
    out = StructureAnalystOutput(scores=[
        ChapterScore(chapter_id="c1", tension=0.2, pacing_label="lull"),
        ChapterScore(chapter_id="c2", tension=0.9, pacing_label="climax"),
    ])
    analyst = StructureAnalyst(FakeRunner(out), read, committer)
    await analyst.run_once()
    await proj.catch_up()
    scores = {s.chapter_id: s for s in await read.list_structure_scores()}
    assert scores["c1"].tension == 0.2 and scores["c2"].pacing_label == "climax"


async def test_commit_drops_score_for_unrequested_chapter_id(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
    await proj.catch_up()
    out = StructureAnalystOutput(scores=[ChapterScore(chapter_id="not-in-batch", tension=0.5, pacing_label="steady")])
    analyst = StructureAnalyst(FakeRunner(out), read, committer)
    await analyst.run_once()
    log = await events.events_since(0)
    assert [e for e in log if e.event_type == EventType.ANNOTATION_STRUCTURE_SCORED] == []


async def test_work_returns_none_and_commit_is_noop_when_no_unscored_chapters(stack):
    events, proj, read, committer = stack
    analyst = StructureAnalyst(FakeRunner(StructureAnalystOutput()), read, committer)
    ctx = await analyst.poll()
    assert ctx["unscored"] == []
    result = await analyst.work(ctx)
    assert result is None
    await analyst.commit(result, ctx)
    assert await events.events_since(0) == []


async def test_commit_emits_agent_remarked_when_feed_note_present(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
    await proj.catch_up()
    out = StructureAnalystOutput(
        scores=[ChapterScore(chapter_id="c1", tension=0.5, pacing_label="steady")],
        feed_note="Chapter one holds steady.",
    )
    analyst = StructureAnalyst(FakeRunner(out), read, committer)
    await analyst.run_once()
    log = await events.events_since(0)
    remarks = [e for e in log if e.event_type == EventType.AGENT_REMARKED]
    assert len(remarks) == 1 and remarks[0].payload["note"] == "Chapter one holds steady."


async def test_work_prompt_includes_personality_when_set(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
    await proj.catch_up()
    runner = FakeRunner(StructureAnalystOutput())
    analyst = StructureAnalyst(runner, read, committer, personality="A clinical pacing critic.")
    ctx = await analyst.poll()
    await analyst.work(ctx)
    sent = runner.calls[-1]["messages"][0]["content"]
    assert "A clinical pacing critic." in sent


async def test_commit_propagates_validation_error_and_commits_nothing_for_the_bad_score(stack):
    """A malformed score (out-of-range tension) reaching commit() — e.g. a future
    lenient runner that skips ChapterScore's own Field(ge=0.0, le=1.0) bound --
    still fails fast at AnnotationStructureScored construction, and the exception
    is not swallowed inside the agent: it propagates out of run_once() uncaught."""
    import pytest
    from pydantic import ValidationError
    from types import SimpleNamespace

    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
    await proj.catch_up()
    bad_out = SimpleNamespace(
        scores=[SimpleNamespace(chapter_id="c1", tension=1.5, pacing_label="off the charts")],
        feed_note="",
    )
    analyst = StructureAnalyst(FakeRunner(bad_out), read, committer)
    with pytest.raises(ValidationError):
        await analyst.run_once()
    log = await events.events_since(0)
    assert [e for e in log if e.event_type == EventType.ANNOTATION_STRUCTURE_SCORED] == []


async def test_flags_from_structured_output_are_filed(stack):
    events, proj, read, committer = stack
    from novelizer.agents.schemas import FlagDraft

    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
    await proj.catch_up()
    out = StructureAnalystOutput(scores=[ChapterScore(chapter_id="c1", tension=0.5, pacing_label="steady")], flags=[
        FlagDraft(category="pacing", description="Act 2 sags for six chapters",
                  related_entry_ids=[], proposed_resolution="cut or merge two middle chapters"),
    ])
    analyst = StructureAnalyst(FakeRunner(out), read, committer)
    await analyst.run_once()
    await proj.catch_up()
    flags = await read.list_flags(category="pacing", status="open")
    assert len(flags) == 1
    assert flags[0].description == "Act 2 sags for six chapters"
    assert flags[0].filed_by == "structure_analyst"


async def test_push_mode_recap_uses_summary_when_available(stack):
    from novelizer.canon.events import ChapterSummarized

    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="x" * 20000))
    await events.append(
        EventType.CHAPTER_SUMMARIZED, "c1",
        ChapterSummarized(chapter_id="c1", gist="ch1 gist", summary="A concise recap of chapter one."),
    )
    await proj.catch_up()
    runner = FakeRunner(StructureAnalystOutput())
    analyst = StructureAnalyst(runner, read, committer, pull_mode=False)
    ctx = await analyst.poll()
    await analyst.work(ctx)
    sent = runner.calls[-1]["messages"][0]["content"]
    assert "A concise recap of chapter one." in sent
    assert "x" * 200 not in sent
    assert "Chapter id:c1" in sent


async def test_push_mode_recap_labels_missing_summary(stack):
    from novelizer.brain.context_assembly import ELISION_MARKER

    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="x" * 20000))
    await proj.catch_up()
    runner = FakeRunner(StructureAnalystOutput())
    analyst = StructureAnalyst(runner, read, committer, pull_mode=False)
    ctx = await analyst.poll()
    await analyst.work(ctx)
    sent = runner.calls[-1]["messages"][0]["content"]
    assert ELISION_MARKER in sent
    assert "Chapter id:c1" in sent


class _FakeSettings:
    agent_model = "gpt-4o-mini"
    llm_base_url = None
    llm_api_key = "test-key"
    agent_temperature = 0.7
    llm_max_tokens = None


def test_build_structure_analyst_runner_without_backend_stays_constructible():
    from novelizer.agents.structure_analyst import build_structure_analyst_runner

    runner = build_structure_analyst_runner(_FakeSettings())
    assert runner is not None


def test_build_structure_analyst_runner_with_backend_uses_retrieval_note_base():
    from novelizer.agents.structure_analyst import build_structure_analyst_runner, SYSTEM_PROMPT
    from novelizer.agents.author import RETRIEVAL_NOTE_BASE
    from novelizer.canon_fs.backend import CanonBackend

    backend = CanonBackend(read_store=None)
    runner = build_structure_analyst_runner(_FakeSettings(), backend=backend, tools=[])
    assert runner is not None
    assert "chapter list below" not in RETRIEVAL_NOTE_BASE
    assert (SYSTEM_PROMPT + RETRIEVAL_NOTE_BASE).endswith(RETRIEVAL_NOTE_BASE)


def test_build_structure_analyst_runner_with_backend_bounds_recursion():
    from novelizer.agents.structure_analyst import build_structure_analyst_runner
    from novelizer.canon_fs.backend import CanonBackend

    backend = CanonBackend(read_store=None)
    runner = build_structure_analyst_runner(_FakeSettings(), backend=backend, tools=[])
    assert runner.config.get("recursion_limit") == 100


def test_spec_carries_subagent_grant():
    from novelizer.agents.structure_analyst import SPEC
    assert SPEC.subagent_grant.enabled_setting == "structure_analyst_subagent_enabled"
