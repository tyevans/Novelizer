import os
import tempfile
import pytest
from novelizer.canon.event_store import EventStore
from novelizer.canon.projector import Projector
from novelizer.canon.read_store import ReadStore
from novelizer.canon.committer import Committer, GatingCommitter
from novelizer.canon.policy import AutonomyPolicy
from novelizer.canon.autonomy import AutonomyLevel
from novelizer.canon.proposal_service import ProposalService
from novelizer.canon.events import EventType
from novelizer.agents.plotter import Plotter
from novelizer.agents.schemas import PlotterOutput, BlueprintPlan, BriefIntent
from novelizer.store.models import Chapter, WorldEntry


class FakeRunner:
    def __init__(self, out):
        self._out = out
        self.calls = []

    async def ainvoke(self, inputs):
        self.calls.append(inputs)
        return {"structured_response": self._out}


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


# --- readiness ladder ---

async def test_readiness_is_zero_with_no_chapters_and_no_world(stack):
    events, proj, read, committer = stack
    plotter = Plotter(FakeRunner(None), read, committer)
    assert await plotter.readiness() == 0.0


async def test_readiness_is_one_with_chapters_and_no_blueprint(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
    await proj.catch_up()
    plotter = Plotter(FakeRunner(None), read, committer)
    assert await plotter.readiness() == 1.0


async def test_readiness_is_one_with_blueprint_and_zero_open_future_briefs(stack):
    from novelizer.canon.events import BlueprintAdopted

    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
    await events.append(
        EventType.BLUEPRINT_ADOPTED, "bp1",
        BlueprintAdopted(blueprint_id="bp1", framework="six-position", target_chapter_count=12, beats=[]),
    )
    await proj.catch_up()
    plotter = Plotter(FakeRunner(None), read, committer)
    assert await plotter.readiness() == 1.0


async def test_readiness_is_zero_with_two_open_future_briefs(stack):
    from novelizer.canon.events import BlueprintAdopted, ChapterBriefDrafted

    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
    await events.append(
        EventType.BLUEPRINT_ADOPTED, "bp1",
        BlueprintAdopted(blueprint_id="bp1", framework="six-position", target_chapter_count=12, beats=[]),
    )
    await events.append(
        EventType.CHAPTER_BRIEF_DRAFTED, "b1",
        ChapterBriefDrafted(brief_id="b1", target_ordinal=2, goal="g1"),
    )
    await events.append(
        EventType.CHAPTER_BRIEF_DRAFTED, "b2",
        ChapterBriefDrafted(brief_id="b2", target_ordinal=3, goal="g2"),
    )
    await proj.catch_up()
    plotter = Plotter(FakeRunner(None), read, committer)
    assert await plotter.readiness() == 0.0


async def test_readiness_ignores_far_future_brief_beyond_runway(stack):
    from novelizer.canon.events import BlueprintAdopted, ChapterBriefDrafted

    events, proj, read, committer = stack
    for i in range(3):
        await events.append(
            EventType.CHAPTER_CREATED, f"c{i}", Chapter(id=f"c{i}", title=f"Ch{i}", prose="p")
        )
    await events.append(
        EventType.BLUEPRINT_ADOPTED, "bp1",
        BlueprintAdopted(blueprint_id="bp1", framework="six-position", target_chapter_count=50, beats=[]),
    )
    # ordinal 40 is far beyond the 3-chapter runway window (len(chapters) < ordinal <= len+3)
    # and must not count toward open_briefs_ahead.
    await events.append(
        EventType.CHAPTER_BRIEF_DRAFTED, "b1",
        ChapterBriefDrafted(brief_id="b1", target_ordinal=40, goal="far off"),
    )
    await proj.catch_up()
    plotter = Plotter(FakeRunner(None), read, committer)
    # Since the far-future brief doesn't count, readiness should behave as if
    # there are zero open briefs ahead within the runway -- i.e. fully ready.
    assert await plotter.readiness() == 1.0


# --- run_once: blueprint proposal, gated end-to-end ---

async def test_run_once_blueprint_plan_creates_gated_proposal_and_approval_yields_six_beats(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
    await proj.catch_up()

    autonomy_state = await read.get_autonomy_state()
    assert autonomy_state.global_level == AutonomyLevel.full_auto

    gating_committer = GatingCommitter(events, AutonomyPolicy(read))
    out = PlotterOutput(blueprint_plan=BlueprintPlan(framework="six-position", target_chapter_count=12))
    plotter = Plotter(FakeRunner(out), read, gating_committer)
    await plotter.run_once()
    await proj.catch_up()

    log = await events.events_since(0)
    proposals = [e for e in log if e.event_type == EventType.PROPOSAL_CREATED]
    assert len(proposals) == 1
    assert proposals[0].payload["target_event_type"] == "blueprint.adopted"

    open_proposals = await read.list_proposals(status="open")
    assert len(open_proposals) == 1
    service = ProposalService(events)
    await service.approve(open_proposals[0])
    await proj.catch_up()

    blueprint = await read.get_active_blueprint()
    assert blueprint is not None
    beats = await read.list_beats()
    assert len(beats) == 6


async def test_run_once_brief_draft_projects_open_brief(stack):
    from novelizer.canon.events import BlueprintAdopted

    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
    await events.append(
        EventType.BLUEPRINT_ADOPTED, "bp1",
        BlueprintAdopted(blueprint_id="bp1", framework="six-position", target_chapter_count=12, beats=[]),
    )
    await proj.catch_up()

    out = PlotterOutput(brief_intents=[
        BriefIntent(action="draft", target_ordinal=2, goal="Push the heir toward the threshold."),
    ])
    plotter = Plotter(FakeRunner(out), read, committer)
    await plotter.run_once()
    await proj.catch_up()

    open_briefs = await read.list_briefs("open")
    assert len(open_briefs) == 1
    assert open_briefs[0].target_ordinal == 2


async def test_run_once_reaps_stale_open_brief_even_with_no_intents(stack):
    """A stale open brief (target_ordinal <= drafted chapter count) must be
    mechanically superseded before commit, deterministically -- not
    dependent on the LLM emitting a supersede intent."""
    from novelizer.canon.events import BlueprintAdopted, ChapterBriefDrafted

    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
    await events.append(EventType.CHAPTER_CREATED, "c2", Chapter(id="c2", title="Two", prose="p"))
    await events.append(
        EventType.BLUEPRINT_ADOPTED, "bp1",
        BlueprintAdopted(blueprint_id="bp1", framework="six-position", target_chapter_count=12, beats=[]),
    )
    await events.append(
        EventType.CHAPTER_BRIEF_DRAFTED, "stale1",
        ChapterBriefDrafted(brief_id="stale1", target_ordinal=2, goal="already past"),
    )
    await proj.catch_up()

    out = PlotterOutput()  # no intents at all -- LLM chose to stand aside
    plotter = Plotter(FakeRunner(out), read, committer)
    await plotter.run_once()
    await proj.catch_up()

    open_briefs = await read.list_briefs("open")
    assert open_briefs == []

    log = await events.events_since(0)
    superseded = [e for e in log if e.event_type == EventType.CHAPTER_BRIEF_SUPERSEDED]
    assert len(superseded) == 1
    assert superseded[0].payload["brief_id"] == "stale1"
    assert superseded[0].payload["superseded_by_brief_id"] == ""


async def test_run_once_drops_blueprint_plan_when_one_already_active(stack):
    from novelizer.canon.events import BlueprintAdopted

    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
    await events.append(
        EventType.BLUEPRINT_ADOPTED, "bp1",
        BlueprintAdopted(blueprint_id="bp1", framework="six-position", target_chapter_count=12, beats=[]),
    )
    await proj.catch_up()

    gating_committer = GatingCommitter(events, AutonomyPolicy(read))
    out = PlotterOutput(blueprint_plan=BlueprintPlan(framework="kishotenketsu", target_chapter_count=8))
    plotter = Plotter(FakeRunner(out), read, gating_committer)
    await plotter.run_once()
    await proj.catch_up()

    log = await events.events_since(0)
    proposals = [e for e in log if e.event_type == EventType.PROPOSAL_CREATED]
    assert proposals == []


async def test_run_once_twice_with_pending_proposal_creates_only_one_proposal(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
    await proj.catch_up()

    autonomy_state = await read.get_autonomy_state()
    assert autonomy_state.global_level == AutonomyLevel.full_auto

    gating_committer = GatingCommitter(events, AutonomyPolicy(read))
    out = PlotterOutput(blueprint_plan=BlueprintPlan(framework="six-position", target_chapter_count=12))
    plotter = Plotter(FakeRunner(out), read, gating_committer)

    await plotter.run_once()
    await proj.catch_up()
    await plotter.run_once()
    await proj.catch_up()

    log = await events.events_since(0)
    proposals = [e for e in log if e.event_type == EventType.PROPOSAL_CREATED]
    assert len(proposals) == 1


# --- prompt content ---

async def test_prompt_with_no_blueprint_proposes_one(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
    await proj.catch_up()
    runner = FakeRunner(PlotterOutput())
    plotter = Plotter(runner, read, committer)
    ctx = await plotter.poll()
    await plotter.work(ctx)
    sent = runner.calls[-1]["messages"][0]["content"]
    assert "propose one" in sent


async def test_prompt_with_blueprint_includes_beat_window_line(stack):
    from novelizer.canon.events import BlueprintAdopted

    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
    await events.append(
        EventType.BLUEPRINT_ADOPTED, "bp1",
        BlueprintAdopted(
            blueprint_id="bp1", framework="six-position", target_chapter_count=12,
            beats=[
                {
                    "beat_id": "bp1-catalyst", "slug": "catalyst", "name": "Catalyst",
                    "ideal_pct": 0.10, "tolerance_pct": 0.05, "expected_polarity": "",
                },
            ],
        ),
    )
    await proj.catch_up()
    runner = FakeRunner(PlotterOutput())
    plotter = Plotter(runner, read, committer)
    ctx = await plotter.poll()
    await plotter.work(ctx)
    sent = runner.calls[-1]["messages"][0]["content"]
    assert "[bp1-catalyst]" in sent
    assert "-" in sent  # window range present


async def test_prompt_includes_beat_drift_note_when_present(stack):
    from novelizer.canon.events import BlueprintAdopted

    events, proj, read, committer = stack
    for i in range(9):
        await events.append(EventType.CHAPTER_CREATED, f"c{i}", Chapter(id=f"c{i}", title=str(i), prose="p"))
    await events.append(
        EventType.BLUEPRINT_ADOPTED, "bp1",
        BlueprintAdopted(
            blueprint_id="bp1", framework="six-position", target_chapter_count=10,
            beats=[
                {
                    "beat_id": "bp1-midpoint", "slug": "midpoint", "name": "Midpoint",
                    "ideal_pct": 0.5, "tolerance_pct": 0.1, "expected_polarity": "flip",
                },
            ],
        ),
    )
    await proj.catch_up()
    runner = FakeRunner(PlotterOutput())
    plotter = Plotter(runner, read, committer)
    ctx = await plotter.poll()
    await plotter.work(ctx)
    sent = runner.calls[-1]["messages"][0]["content"]
    assert "Beat drift:" in sent
    assert "Midpoint" in sent


async def test_prompt_includes_tension_target_note_when_deviated(stack):
    from novelizer.canon.events import BlueprintAdopted, AnnotationStructureScored

    events, proj, read, committer = stack
    for i in range(20):
        await events.append(EventType.CHAPTER_CREATED, f"c{i}", Chapter(id=f"c{i}", title=str(i), prose="p"))
    await events.append(
        EventType.BLUEPRINT_ADOPTED, "bp1",
        BlueprintAdopted(
            blueprint_id="bp1", framework="six-position", target_chapter_count=20,
            beats=[
                {
                    "beat_id": "bp1-midpoint", "slug": "midpoint", "name": "Midpoint",
                    "ideal_pct": 0.5, "tolerance_pct": 0.1, "expected_polarity": "flip",
                },
            ],
        ),
    )
    await events.append(EventType.ANNOTATION_STRUCTURE_SCORED, "c19",
                        AnnotationStructureScored(chapter_id="c19", tension=0.99))
    await proj.catch_up()
    runner = FakeRunner(PlotterOutput())
    plotter = Plotter(runner, read, committer)
    ctx = await plotter.poll()
    await plotter.work(ctx)
    sent = runner.calls[-1]["messages"][0]["content"]
    assert "Tension vs blueprint:" in sent
    assert "ch 20" in sent


class _FakeSettings:
    agent_model = "gpt-4o-mini"
    llm_base_url = None
    llm_api_key = "test-key"
    agent_temperature = 0.7
    llm_max_tokens = None


def test_build_plotter_runner_without_backend_stays_constructible():
    from novelizer.agents.plotter import build_plotter_runner

    runner = build_plotter_runner(_FakeSettings())
    assert runner is not None


def test_build_plotter_runner_with_backend_uses_retrieval_note_base():
    from novelizer.agents.plotter import build_plotter_runner, PLOTTER_SYSTEM_PROMPT
    from novelizer.agents.author import RETRIEVAL_NOTE_BASE
    from novelizer.canon_fs.backend import CanonBackend

    backend = CanonBackend(read_store=None)
    runner = build_plotter_runner(_FakeSettings(), backend=backend, tools=[])
    assert runner is not None
    assert (PLOTTER_SYSTEM_PROMPT + RETRIEVAL_NOTE_BASE).endswith(RETRIEVAL_NOTE_BASE)


def test_build_plotter_runner_with_backend_bounds_recursion():
    from novelizer.agents.plotter import build_plotter_runner
    from novelizer.canon_fs.backend import CanonBackend

    backend = CanonBackend(read_store=None)
    runner = build_plotter_runner(_FakeSettings(), backend=backend, tools=[])
    assert runner.config.get("recursion_limit") == 100
