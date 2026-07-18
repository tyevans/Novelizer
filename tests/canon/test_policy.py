import pytest
from novelizer.canon.autonomy import AutonomyLevel, AutonomyState
from novelizer.canon.events import EventType
from novelizer.canon.policy import AutonomyPolicy


class FakeRead:
    def __init__(self, state: AutonomyState):
        self._state = state

    async def get_autonomy_state(self):
        return self._state


GATED_CASES = [
    # (level, event_type, expected_gated)
    (AutonomyLevel.full_auto, EventType.CHAPTER_CREATED, False),
    (AutonomyLevel.full_auto, EventType.RETCON_REQUEST_RESOLVED, False),
    (AutonomyLevel.gated_retcons, EventType.RETCON_REQUEST_RESOLVED, True),
    (AutonomyLevel.gated_retcons, EventType.WORLD_ENTRY_SUPERSEDED, True),
    (AutonomyLevel.gated_retcons, EventType.CHAPTER_CREATED, False),
    (AutonomyLevel.gated_retcons, EventType.WORLD_ENTRY_CREATED, False),
    (AutonomyLevel.gated_canon, EventType.WORLD_ENTRY_CREATED, True),
    (AutonomyLevel.gated_canon, EventType.CHARACTER_UPDATED, True),
    (AutonomyLevel.gated_canon, EventType.CHAPTER_CREATED, True),
    (AutonomyLevel.gated_canon, EventType.CHAPTER_STATUS_CHANGED, True),
    (AutonomyLevel.gated_canon, EventType.RETCON_REQUEST_RESOLVED, True),
    (AutonomyLevel.gated_all, EventType.CHAPTER_CREATED, True),
    (AutonomyLevel.gated_all, EventType.RETCON_REQUEST_CREATED, True),
]


@pytest.mark.parametrize("level,event_type,expected", GATED_CASES)
async def test_is_gated_by_level(level, event_type, expected):
    policy = AutonomyPolicy(FakeRead(AutonomyState(global_level=level)))
    assert await policy.is_gated("author", event_type) is expected


@pytest.mark.parametrize("level", list(AutonomyLevel))
async def test_director_signals_are_never_gated(level):
    policy = AutonomyPolicy(FakeRead(AutonomyState(global_level=level)))
    assert await policy.is_gated("author", EventType.DIRECTOR_SIGNAL_CREATED) is False
    assert await policy.is_gated("any_agent", EventType.DIRECTOR_SIGNAL_CONSUMED) is False


async def test_per_agent_override_takes_precedence():
    state = AutonomyState(global_level=AutonomyLevel.full_auto,
                           overrides={"retconner": AutonomyLevel.gated_all})
    policy = AutonomyPolicy(FakeRead(state))
    assert await policy.is_gated("retconner", EventType.RETCON_REQUEST_RESOLVED) is True
    assert await policy.is_gated("author", EventType.CHAPTER_CREATED) is False


@pytest.mark.parametrize("level", list(AutonomyLevel))
async def test_agent_remarked_is_never_gated(level):
    policy = AutonomyPolicy(FakeRead(AutonomyState(global_level=level)))
    assert await policy.is_gated("any_agent", EventType.AGENT_REMARKED) is False


@pytest.mark.parametrize("level", list(AutonomyLevel))
@pytest.mark.parametrize("event_type", [
    EventType.THREAD_PLANTED, EventType.THREAD_TOUCHED,
    EventType.THREAD_PAID_OFF, EventType.THREAD_ABANDONED,
])
async def test_thread_events_are_never_gated(level, event_type):
    policy = AutonomyPolicy(FakeRead(AutonomyState(global_level=level)))
    assert await policy.is_gated("author", event_type) is False
    assert await policy.is_gated("editor", event_type) is False
