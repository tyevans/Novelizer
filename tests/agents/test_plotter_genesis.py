import pytest
from novelizer.agents.plotter import Plotter
from novelizer.store.models import BlueprintRecord, DirectorSignal, SignalKind
from novelizer.canon.events import EventType


class _Read:
    def __init__(self, *, chapters=None, world=None, blueprint=None,
                 proposals=None, signals=None, briefs=None):
        self._chapters = chapters or []
        self._world = world or []
        self._blueprint = blueprint
        self._proposals = proposals or []
        self._signals = signals or []
        self._briefs = briefs or []

    async def list_chapters(self, status=None):
        return self._chapters

    async def list_world_entries(self):
        return self._world

    async def get_active_blueprint(self):
        return self._blueprint

    async def list_proposals(self, status=None):
        return [p for p in self._proposals if status is None or p.status == status]

    async def list_unconsumed_signals(self, target_agent=None):
        return self._signals

    async def list_briefs(self, status=None):
        return self._briefs


class _Prop:
    def __init__(self):
        self.status = "open"
        self.target_event_type = EventType.BLUEPRINT_ADOPTED


def _plotter(read):
    p = Plotter.__new__(Plotter)
    p._read = read
    return p


def _seed():
    return DirectorSignal(kind=SignalKind.seed, body="a lighthouse keeper who taxes the tide")


@pytest.mark.asyncio
async def test_genesis_wakes_on_premise_seed():
    p = _plotter(_Read(signals=[_seed()]))
    assert await p.readiness() == 1.0


@pytest.mark.asyncio
async def test_genesis_idle_without_premise():
    p = _plotter(_Read())               # no seed, no world, no chapters
    assert await p.readiness() == 0.0


@pytest.mark.asyncio
async def test_ready_to_draft_first_briefs_right_after_blueprint_adoption():
    """Outline-first genesis: once a blueprint is active, the Plotter is
    immediately ready (1.0) even with no chapters and no world entries yet --
    that's exactly the moment it needs to draft the first chapter briefs so
    the Author has assignments. This is the reachable state right after
    blueprint adoption, before the World Architect creates entries and before
    the Author drafts chapter 1. Deliberately different from the old
    pre-blueprint 0.0 guard, which does not apply once a blueprint exists."""
    blueprint = BlueprintRecord(id="bp1", framework="three-act", target_chapter_count=12)
    p = _plotter(_Read(blueprint=blueprint, chapters=[], world=[], briefs=[]))
    assert await p.readiness() == 1.0


@pytest.mark.asyncio
async def test_stands_down_while_blueprint_proposal_pending():
    p = _plotter(_Read(signals=[_seed()], proposals=[_Prop()]))
    assert await p.readiness() == 0.0


@pytest.mark.asyncio
async def test_commit_does_not_consume_seed_signals():
    """Behavioral: drive the real commit() wiring, not a re-derived filter
    expression. We spy on _consume_signals and assert the list commit()
    actually passes it excludes the seed and includes the non-seed signal."""
    from novelizer.agents.schemas import PlotterOutput

    seed = DirectorSignal(kind=SignalKind.seed, body="premise")
    focus = DirectorSignal(kind=SignalKind.focus, body="focus on the harbor", target_agent="plotter")

    p = _plotter(_Read())
    p.name = "plotter"
    p._committer = None  # unused: brief/beat/resolution/promise intent lists are all empty
    consumed = []

    async def fake_consume(sigs):
        consumed.extend(sigs)

    p._consume_signals = fake_consume

    async def _noop(*args, **kwargs):
        return None

    # commit() touches these helpers before reaching the consume call; stub
    # them out so we can drive the real method with a minimal, empty-intent
    # PlotterOutput and reach the tail-end _consume_signals call.
    p._reap_stale_open_briefs = _noop
    p._declare_completion_if_satisfied = _noop
    p._remark = _noop

    out = PlotterOutput()
    ctx = {
        "chapters": [],
        "blueprint": None,
        "open_briefs": [],
        "threads": [],
        "beats": [],
        "promises": [],
        "secrets": [],
        "open_proposals": [],
        "signals": [seed, focus],
    }

    await p.commit(out, ctx)

    assert seed not in consumed
    assert focus in consumed
