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
    (AutonomyLevel.full_auto, EventType.FLAG_RESOLVED, False),
    (AutonomyLevel.gated_retcons, EventType.FLAG_RESOLVED, True),
    (AutonomyLevel.gated_retcons, EventType.WORLD_ENTRY_SUPERSEDED, True),
    (AutonomyLevel.gated_retcons, EventType.CHAPTER_CREATED, False),
    (AutonomyLevel.gated_retcons, EventType.WORLD_ENTRY_CREATED, False),
    (AutonomyLevel.gated_canon, EventType.WORLD_ENTRY_CREATED, True),
    (AutonomyLevel.gated_canon, EventType.CHARACTER_UPDATED, True),
    (AutonomyLevel.gated_canon, EventType.CHAPTER_CREATED, True),
    (AutonomyLevel.gated_canon, EventType.CHAPTER_STATUS_CHANGED, True),
    (AutonomyLevel.gated_canon, EventType.CHAPTER_REVISED, True),
    (AutonomyLevel.gated_canon, EventType.FLAG_RESOLVED, True),
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
    assert await policy.is_gated("retconner", EventType.FLAG_RESOLVED) is True
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


@pytest.mark.parametrize("level", list(AutonomyLevel))
async def test_annotation_structure_scored_is_never_gated(level):
    policy = AutonomyPolicy(FakeRead(AutonomyState(global_level=level)))
    assert await policy.is_gated("structure_analyst", EventType.ANNOTATION_STRUCTURE_SCORED) is False


@pytest.mark.parametrize("level", list(AutonomyLevel))
@pytest.mark.parametrize("event_type", [
    EventType.SECRET_CREATED, EventType.SECRET_LEARNED,
    EventType.SECRET_REFERENCED, EventType.CAUSAL_EDGE_DECLARED,
])
async def test_knowledge_bookkeeping_events_are_never_gated(level, event_type):
    policy = AutonomyPolicy(FakeRead(AutonomyState(global_level=level)))
    assert await policy.is_gated("author", event_type) is False
    assert await policy.is_gated("editor", event_type) is False
    assert await policy.is_gated("character_keeper", event_type) is False


async def test_secret_revealed_is_gated_under_gated_canon_and_gated_all():
    policy = AutonomyPolicy(FakeRead(AutonomyState(global_level=AutonomyLevel.gated_canon)))
    assert await policy.is_gated("author", EventType.SECRET_REVEALED) is True
    policy_all = AutonomyPolicy(FakeRead(AutonomyState(global_level=AutonomyLevel.gated_all)))
    assert await policy_all.is_gated("author", EventType.SECRET_REVEALED) is True


async def test_secret_revealed_is_not_gated_under_full_auto_or_gated_retcons():
    policy_full = AutonomyPolicy(FakeRead(AutonomyState(global_level=AutonomyLevel.full_auto)))
    assert await policy_full.is_gated("author", EventType.SECRET_REVEALED) is False
    policy_retcons = AutonomyPolicy(FakeRead(AutonomyState(global_level=AutonomyLevel.gated_retcons)))
    assert await policy_retcons.is_gated("author", EventType.SECRET_REVEALED) is False


@pytest.mark.parametrize("level", list(AutonomyLevel))
async def test_chapter_mined_is_never_gated(level):
    policy = AutonomyPolicy(FakeRead(AutonomyState(global_level=level)))
    assert await policy.is_gated("continuity_checker", EventType.CHAPTER_MINED) is False


@pytest.mark.parametrize("level", list(AutonomyLevel))
async def test_theme_introduced_is_never_gated(level):
    policy = AutonomyPolicy(FakeRead(AutonomyState(global_level=level)))
    assert await policy.is_gated("author", EventType.THEME_INTRODUCED) is False


@pytest.mark.parametrize("level", list(AutonomyLevel))
async def test_theme_developed_is_never_gated(level):
    policy = AutonomyPolicy(FakeRead(AutonomyState(global_level=level)))
    assert await policy.is_gated("editor", EventType.THEME_DEVELOPED) is False


@pytest.mark.parametrize("level", list(AutonomyLevel))
@pytest.mark.parametrize("event_type", [
    EventType.INSPIRATION_DRAWN, EventType.INSPIRATION_HAND_CONSUMED,
    EventType.INSPIRATION_HAND_SUPERSEDED, EventType.INSPIRATION_UPTAKE_RECORDED,
])
async def test_inspiration_events_are_never_gated(level, event_type):
    policy = AutonomyPolicy(FakeRead(AutonomyState(global_level=level)))
    assert await policy.is_gated("muse", event_type) is False


@pytest.mark.parametrize("level", list(AutonomyLevel))
@pytest.mark.parametrize("event_type", [
    EventType.PROMISE_MADE, EventType.PROMISE_PROGRESSED,
    EventType.PROMISE_PAID, EventType.PROMISE_RELEASED,
    EventType.THREAD_RESOLUTION_PLANNED, EventType.SECRET_REVEAL_PLANNED,
])
async def test_promise_and_planning_events_are_never_gated(level, event_type):
    policy = AutonomyPolicy(FakeRead(AutonomyState(global_level=level)))
    assert await policy.is_gated("author", event_type) is False


@pytest.mark.parametrize("level", list(AutonomyLevel))
@pytest.mark.parametrize("event_type", [
    EventType.ARC_DECLARED, EventType.ARC_PIVOT_PLANNED,
    EventType.ARC_ADVANCED, EventType.ARC_RESOLVED,
])
async def test_arc_events_are_never_gated(level, event_type):
    policy = AutonomyPolicy(FakeRead(AutonomyState(global_level=level)))
    assert await policy.is_gated("character_keeper", event_type) is False


@pytest.mark.parametrize("level", list(AutonomyLevel))
async def test_blueprint_adopted_is_gated_at_every_level_including_full_auto(level):
    policy = AutonomyPolicy(FakeRead(AutonomyState(global_level=level)))
    assert await policy.is_gated("plotter", EventType.BLUEPRINT_ADOPTED) is True


@pytest.mark.parametrize("level", list(AutonomyLevel))
@pytest.mark.parametrize("event_type", [
    EventType.BLUEPRINT_RETARGETED, EventType.BEAT_FULFILLED,
    EventType.CHAPTER_BRIEF_DRAFTED, EventType.CHAPTER_BRIEF_SUPERSEDED,
    EventType.CHAPTER_BRIEF_FULFILLED,
])
async def test_other_blueprint_events_are_never_gated(level, event_type):
    policy = AutonomyPolicy(FakeRead(AutonomyState(global_level=level)))
    assert await policy.is_gated("plotter", event_type) is False


@pytest.mark.parametrize("level", list(AutonomyLevel))
async def test_book_completed_is_never_gated(level):
    policy = AutonomyPolicy(FakeRead(AutonomyState(global_level=level)))
    assert await policy.is_gated("plotter", EventType.BOOK_COMPLETED) is False


@pytest.mark.parametrize("level", list(AutonomyLevel))
@pytest.mark.parametrize("event_type", [
    EventType.FLAG_ESCALATED, EventType.FLAG_ESCALATION_CLEARED,
])
async def test_flag_escalation_events_are_never_gated(level, event_type):
    policy = AutonomyPolicy(FakeRead(AutonomyState(global_level=level)))
    assert await policy.is_gated("triage", event_type) is False
    assert await policy.is_gated("author", event_type) is False
