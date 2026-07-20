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

async def test_readiness_raised_by_late_beat_drift_when_runway_full(stack):
    from novelizer.canon.events import BlueprintAdopted, BeatSpec, ChapterBriefDrafted

    events, proj, read, committer = stack
    for i in range(2):
        await events.append(
            EventType.CHAPTER_CREATED, f"c{i}", Chapter(id=f"c{i}", title=f"Ch{i}", prose="p")
        )
    await events.append(
        EventType.BLUEPRINT_ADOPTED, "bp1",
        BlueprintAdopted(
            blueprint_id="bp1", framework="six-position", target_chapter_count=10,
            beats=[BeatSpec(
                beat_id="bp1-beat1", slug="beat1", name="Beat1",
                ideal_pct=0.0, tolerance_pct=0.0,
            )],
        ),
    )
    # Fill the brief runway so the plain runway computation alone would be 0.0.
    await events.append(
        EventType.CHAPTER_BRIEF_DRAFTED, "b1",
        ChapterBriefDrafted(brief_id="b1", target_ordinal=3, goal="g1"),
    )
    await events.append(
        EventType.CHAPTER_BRIEF_DRAFTED, "b2",
        ChapterBriefDrafted(brief_id="b2", target_ordinal=4, goal="g2"),
    )
    await proj.catch_up()
    plotter = Plotter(FakeRunner(None), read, committer)
    # Beat1's window closes at chapter 1; with 2 chapters drafted and it still
    # unfulfilled, beat_drifts should report "late" and raise readiness.
    assert await plotter.readiness() >= 0.9


async def test_readiness_unchanged_when_runway_full_and_no_drift(stack):
    from novelizer.canon.events import BlueprintAdopted, ChapterBriefDrafted

    events, proj, read, committer = stack
    for i in range(2):
        await events.append(
            EventType.CHAPTER_CREATED, f"c{i}", Chapter(id=f"c{i}", title=f"Ch{i}", prose="p")
        )
    await events.append(
        EventType.BLUEPRINT_ADOPTED, "bp1",
        BlueprintAdopted(
            blueprint_id="bp1", framework="six-position", target_chapter_count=10, beats=[],
        ),
    )
    await events.append(
        EventType.CHAPTER_BRIEF_DRAFTED, "b1",
        ChapterBriefDrafted(brief_id="b1", target_ordinal=3, goal="g1"),
    )
    await events.append(
        EventType.CHAPTER_BRIEF_DRAFTED, "b2",
        ChapterBriefDrafted(brief_id="b2", target_ordinal=4, goal="g2"),
    )
    await proj.catch_up()
    plotter = Plotter(FakeRunner(None), read, committer)
    assert await plotter.readiness() == 0.0



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


async def test_run_once_retarget_intent_projects_new_target(stack):
    from novelizer.canon.events import BlueprintAdopted
    from novelizer.agents.schemas import RetargetIntent

    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
    await events.append(
        EventType.BLUEPRINT_ADOPTED, "bp1",
        BlueprintAdopted(blueprint_id="bp1", framework="six-position", target_chapter_count=12, beats=[]),
    )
    await proj.catch_up()

    out = PlotterOutput(retarget_intent=RetargetIntent(target_chapter_count=20, reason="running long"))
    plotter = Plotter(FakeRunner(out), read, committer)
    await plotter.run_once()
    await proj.catch_up()

    blueprint = await read.get_active_blueprint()
    assert blueprint.target_chapter_count == 20


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


async def test_run_once_reaps_stale_open_brief_even_when_llm_returns_none(stack):
    """The reap is deterministic housekeeping that must not depend on the
    LLM succeeding at all -- structured_response None (e.g. an LLM/parse
    failure) must still supersede a stale open brief."""
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

    plotter = Plotter(FakeRunner(None), read, committer)  # LLM failure -> structured_response None
    await plotter.run_once()
    await proj.catch_up()

    open_briefs = await read.list_briefs("open")
    assert open_briefs == []

    log = await events.events_since(0)
    superseded = [e for e in log if e.event_type == EventType.CHAPTER_BRIEF_SUPERSEDED]
    assert len(superseded) == 1
    assert superseded[0].payload["brief_id"] == "stale1"


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


async def test_prompt_includes_finale_convergence_note_inside_window(stack):
    from novelizer.canon.events import BlueprintAdopted

    events, proj, read, committer = stack
    for i in range(8):
        await events.append(EventType.CHAPTER_CREATED, f"c{i}", Chapter(id=f"c{i}", title=str(i), prose="p"))
    await events.append(
        EventType.BLUEPRINT_ADOPTED, "bp1",
        BlueprintAdopted(
            blueprint_id="bp1", framework="three_act", target_chapter_count=10,
            beats=[
                {
                    "beat_id": "bp1-open", "slug": "open", "name": "Opening",
                    "ideal_pct": 0.1, "tolerance_pct": 0.05,
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
    assert "Opening" in sent
    assert "chapter" in sent.lower()


async def test_prompt_omits_finale_convergence_note_before_window(stack):
    from novelizer.canon.events import BlueprintAdopted

    events, proj, read, committer = stack
    for i in range(3):
        await events.append(EventType.CHAPTER_CREATED, f"c{i}", Chapter(id=f"c{i}", title=str(i), prose="p"))
    await events.append(
        EventType.BLUEPRINT_ADOPTED, "bp1",
        BlueprintAdopted(
            blueprint_id="bp1", framework="three_act", target_chapter_count=10,
            beats=[
                {
                    "beat_id": "bp1-open", "slug": "open", "name": "Opening",
                    "ideal_pct": 0.1, "tolerance_pct": 0.05,
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
    assert "Steer the remaining" not in sent


class _FakeSettings:
    agent_model = "gpt-4o-mini"
    llm_base_url = None
    llm_api_key = "test-key"
    agent_temperature = 0.7
    llm_max_tokens = None


async def test_prompt_includes_arc_note_when_arc_stagnant(stack):
    from novelizer.canon.events import ArcDeclared
    from novelizer.store.models import Character

    events, proj, read, committer = stack
    for i in range(5):
        await events.append(EventType.CHAPTER_CREATED, f"c{i}", Chapter(id=f"c{i}", title=str(i), prose="p"))
    await events.append(EventType.CHARACTER_CREATED, "mara", Character(id="mara", name="Mara"))
    await events.append(
        EventType.ARC_DECLARED, "arc1",
        ArcDeclared(arc_id="arc1", character_id="mara", arc_type="positive", lie="I am alone"),
    )
    await proj.catch_up()
    runner = FakeRunner(PlotterOutput())
    plotter = Plotter(runner, read, committer)
    ctx = await plotter.poll()
    await plotter.work(ctx)
    sent = runner.calls[-1]["messages"][0]["content"]
    assert "Arc alignment:" in sent
    assert "route Mara into the next brief" in sent


async def test_prompt_omits_arc_note_when_quiet(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "c0", Chapter(id="c0", title="0", prose="p"))
    await proj.catch_up()
    runner = FakeRunner(PlotterOutput())
    plotter = Plotter(runner, read, committer)
    ctx = await plotter.poll()
    await plotter.work(ctx)
    sent = runner.calls[-1]["messages"][0]["content"]
    assert "Arc alignment:" not in sent


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


def test_build_plotter_runner_tooled_branch_passes_plotter_skills(monkeypatch):
    from novelizer.agents import plotter as plotter_mod
    from novelizer.canon_fs.backend import CanonBackend

    captured = {}

    class FakeGraph:
        def with_config(self, config):
            return self

    def fake_create_deep_agent(*, model, system_prompt, response_format, backend=None, tools=None, skills=None):
        captured["skills"] = skills
        return FakeGraph()

    import deepagents
    monkeypatch.setattr(deepagents, "create_deep_agent", fake_create_deep_agent)

    backend = CanonBackend(read_store=None)
    plotter_mod.build_plotter_runner(_FakeSettings(), backend=backend, tools=[])
    assert captured["skills"] == plotter_mod.PLOTTER_SKILLS
    assert captured["skills"] == ["/skills"]


def test_build_plotter_runner_bare_branch_carries_no_skills_kwarg(monkeypatch):
    from novelizer.agents import plotter as plotter_mod

    captured = {}

    class FakeGraph:
        pass

    def fake_create_deep_agent(*, model, system_prompt, response_format):
        captured["called"] = True
        return FakeGraph()

    import deepagents
    monkeypatch.setattr(deepagents, "create_deep_agent", fake_create_deep_agent)

    plotter_mod.build_plotter_runner(_FakeSettings())
    assert captured["called"]


def test_build_plotter_runner_with_real_composite_backend_is_constructible():
    """Integration smoke: build the tooled Plotter runner against the real
    CompositeBackend recipe from Runtime._phase_a_toolkit (canon default +
    /outline/, /skills/, /workspace/ routes). SkillsMiddleware validates its
    sources lazily (at invoke/before_agent time, not construction), so this
    proves the graph is constructible with the real skills backend wired in
    -- not that the skill paths resolve to actual skill packs."""
    from deepagents.backends import CompositeBackend, StateBackend
    from novelizer.agents.plotter import build_plotter_runner
    from novelizer.canon_fs.backend import CanonBackend
    from novelizer.canon_fs.outline import OutlineBackend
    from novelizer.canon_fs.skills_route import build_skills_backend

    backend = CompositeBackend(
        default=CanonBackend(read_store=None),
        routes={
            "/outline/": OutlineBackend(None),
            "/skills/": build_skills_backend(),
            "/workspace/": StateBackend(),
        },
    )
    runner = build_plotter_runner(_FakeSettings(), backend=backend, tools=[])
    assert runner is not None


# --- book.completed emission ---

async def _adopt_satisfied_blueprint(events, proj):
    from novelizer.canon.events import BlueprintAdopted, BeatSpec, BeatFulfilled, PromiseMade, PromisePaid
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
    await events.append(
        EventType.BLUEPRINT_ADOPTED, "bp1",
        BlueprintAdopted(
            blueprint_id="bp1", framework="six-position", target_chapter_count=1,
            beats=[BeatSpec(beat_id="bp1-inciting", slug="inciting", name="Inciting", ideal_pct=0.1, tolerance_pct=0.9)],
        ),
    )
    await events.append(
        EventType.BEAT_FULFILLED, "bp1-inciting",
        BeatFulfilled(beat_id="bp1-inciting", chapter_id="c1"),
    )
    await events.append(EventType.PROMISE_MADE, "pr1", PromiseMade(id="pr1", name="a gun on the mantle"))
    await events.append(EventType.PROMISE_PAID, "pr1", PromisePaid(id="pr1", chapter_id="c1"))
    await proj.catch_up()


async def test_run_once_emits_book_completed_when_story_satisfied(stack):
    events, proj, read, committer = stack
    await _adopt_satisfied_blueprint(events, proj)

    out = PlotterOutput(feed_note="the shape is closed")
    plotter = Plotter(FakeRunner(out), read, committer)
    await plotter.run_once()
    await proj.catch_up()

    log = await events.events_since(0)
    completed = [e for e in log if e.event_type == EventType.BOOK_COMPLETED]
    assert len(completed) == 1
    assert completed[0].payload["blueprint_id"] == "bp1"
    assert completed[0].payload["chapter_id"] == "c1"
    assert completed[0].payload["note"] == "the shape is closed"

    active = await read.get_active_blueprint()
    assert active.completed is True


async def test_run_once_twice_emits_book_completed_only_once(stack):
    events, proj, read, committer = stack
    await _adopt_satisfied_blueprint(events, proj)

    out = PlotterOutput()
    plotter = Plotter(FakeRunner(out), read, committer)
    await plotter.run_once()
    await proj.catch_up()
    await plotter.run_once()
    await proj.catch_up()

    log = await events.events_since(0)
    completed = [e for e in log if e.event_type == EventType.BOOK_COMPLETED]
    assert len(completed) == 1


async def test_run_once_emits_book_completed_even_when_llm_returns_none(stack):
    events, proj, read, committer = stack
    await _adopt_satisfied_blueprint(events, proj)

    plotter = Plotter(FakeRunner(None), read, committer)
    await plotter.run_once()
    await proj.catch_up()

    log = await events.events_since(0)
    completed = [e for e in log if e.event_type == EventType.BOOK_COMPLETED]
    assert len(completed) == 1
    assert completed[0].payload["note"] == ""


async def test_run_once_no_book_completed_when_unsatisfied(stack):
    from novelizer.canon.events import BlueprintAdopted, BeatSpec

    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
    await events.append(
        EventType.BLUEPRINT_ADOPTED, "bp1",
        BlueprintAdopted(
            blueprint_id="bp1", framework="six-position", target_chapter_count=1,
            beats=[BeatSpec(beat_id="bp1-inciting", slug="inciting", name="Inciting", ideal_pct=0.1, tolerance_pct=0.9)],
        ),
    )
    await proj.catch_up()

    out = PlotterOutput()
    plotter = Plotter(FakeRunner(out), read, committer)
    await plotter.run_once()
    await proj.catch_up()

    log = await events.events_since(0)
    completed = [e for e in log if e.event_type == EventType.BOOK_COMPLETED]
    assert completed == []
