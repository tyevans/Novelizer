import pytest
from novelizer.agents.author import Author, _summarize

PROVISIONAL_NOTE = "No outline exists yet — you are drafting ahead of the Plotter under a"


class _Read:
    def __init__(self, *, drafts=0, blueprint=None, proposals=None, world=None):
        self._drafts = drafts
        self._blueprint = blueprint
        self._proposals = proposals or []
        self._world = world or []

    async def list_chapters(self, status=None):
        if status == "draft":
            return [object()] * self._drafts
        return []

    async def get_active_blueprint(self):
        return self._blueprint

    async def list_proposals(self, status=None):
        return [p for p in self._proposals if status is None or p.status == status]

    async def list_world_entries(self):
        return self._world


class _Prop:
    def __init__(self):
        from novelizer.canon.events import EventType
        self.status = "open"
        self.target_event_type = EventType.BLUEPRINT_ADOPTED


def _author(read, gate_enabled=True):
    a = Author.__new__(Author)          # bypass runner/committer wiring
    a._read = read
    a.gate_enabled = gate_enabled
    return a


@pytest.mark.asyncio
async def test_no_blueprint_suppresses_author():
    a = _author(_Read(blueprint=None))
    assert await a.readiness() == 0.0


@pytest.mark.asyncio
async def test_active_blueprint_restores_normal_readiness():
    a = _author(_Read(blueprint=object(), drafts=0))
    assert await a.readiness() == 1.0


@pytest.mark.asyncio
async def test_fallback_opens_when_proposal_and_world_present():
    a = _author(_Read(blueprint=None, proposals=[_Prop()], world=[object()]))
    assert await a.readiness() == 1.0


@pytest.mark.asyncio
async def test_disabled_gate_drafts_without_blueprint():
    a = _author(_Read(blueprint=None), gate_enabled=False)
    assert await a.readiness() == 1.0


@pytest.mark.asyncio
async def test_draft_backlog_decays_readiness_when_gate_open():
    a = _author(_Read(blueprint=object(), drafts=0))
    assert await a.readiness() == 1.0
    a = _author(_Read(blueprint=object(), drafts=1))
    assert await a.readiness() == pytest.approx(1.0 - 1 / 3)
    a = _author(_Read(blueprint=object(), drafts=3))
    assert await a.readiness() == 0.0


def _bare_ctx():
    return {
        "world": [], "characters": [], "previous": [], "chapters": [], "signals": [],
        "threads": [], "secrets": [], "knowledge_matrix": {}, "themes": [], "causal_edges": [],
    }


def test_provisional_note_fires_when_gate_enabled_and_no_blueprint_or_brief():
    out = _summarize(_bare_ctx(), gate_enabled=True)
    assert PROVISIONAL_NOTE in out


def test_provisional_note_absent_when_gate_disabled():
    out = _summarize(_bare_ctx(), gate_enabled=False)
    assert PROVISIONAL_NOTE not in out
