import pytest
from novelizer.brain import gate
from novelizer.canon.events import EventType


class _Prop:
    def __init__(self, status, target_event_type):
        self.status = status
        self.target_event_type = target_event_type


class FakeRead:
    def __init__(self, *, blueprint=None, proposals=None, world=None):
        self._blueprint = blueprint
        self._proposals = proposals or []
        self._world = world or []

    async def get_active_blueprint(self):
        return self._blueprint

    async def list_proposals(self, status=None):
        return [p for p in self._proposals if status is None or p.status == status]

    async def list_world_entries(self):
        return self._world


def _bp_proposal():
    return _Prop("open", EventType.BLUEPRINT_ADOPTED)


@pytest.mark.asyncio
async def test_active_blueprint_lets_author_draft():
    read = FakeRead(blueprint=object())
    assert await gate.has_active_blueprint(read) is True
    assert await gate.author_may_draft(read, gate_enabled=True) is True


@pytest.mark.asyncio
async def test_no_blueprint_no_proposal_blocks_author():
    read = FakeRead()
    assert await gate.author_may_draft(read, gate_enabled=True) is False


@pytest.mark.asyncio
async def test_fallback_opens_when_proposal_pending_and_world_exists():
    read = FakeRead(proposals=[_bp_proposal()], world=[object()])
    assert await gate.genesis_fallback_open(read) is True
    assert await gate.author_may_draft(read, gate_enabled=True) is True


@pytest.mark.asyncio
async def test_fallback_closed_with_proposal_but_no_world():
    read = FakeRead(proposals=[_bp_proposal()], world=[])
    assert await gate.genesis_fallback_open(read) is False
    assert await gate.author_may_draft(read, gate_enabled=True) is False


@pytest.mark.asyncio
async def test_fallback_closed_when_open_proposal_is_not_a_blueprint():
    read = FakeRead(proposals=[_Prop("open", EventType.CHARACTER_CREATED)], world=[object()])
    assert await gate.genesis_fallback_open(read) is False


@pytest.mark.asyncio
async def test_disabled_gate_always_lets_author_draft():
    read = FakeRead()
    assert await gate.author_may_draft(read, gate_enabled=False) is True
