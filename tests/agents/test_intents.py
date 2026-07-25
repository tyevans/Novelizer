import pytest
from novelizer.agents.intents import (
    commit_thread_intents, commit_theme_intents, commit_secret_plants, commit_secret_citations,
    commit_causal_intents,
    commit_promise_intents, commit_blueprint_plan, commit_retarget_intent, commit_brief_intents,
    commit_beat_intents, commit_resolution_plan_intents, commit_arc_intents,
)
from novelizer.agents.schemas import (
    ThreadIntent, ThemeIntent, SecretPlant, SecretCitation, CausalIntent, PromiseIntent,
    BlueprintPlan, RetargetIntent, BriefIntent, BeatIntent, ResolutionPlanIntent, ArcIntent,
)
from novelizer.canon.events import EventType
from novelizer.canon.beat_templates import BEAT_TEMPLATES
from novelizer.store.models import ChapterBriefRecord, BriefStatus, BlueprintRecord


class FakeCommitter:
    def __init__(self):
        self.commits = []

    async def commit(self, agent_name, event_type, aggregate_id, payload):
        self.commits.append((agent_name, event_type, aggregate_id, payload))


@pytest.mark.asyncio
async def test_thread_plant_mints_and_touch_requires_known_id():
    c = FakeCommitter()
    await commit_thread_intents(
        c, "author",
        [ThreadIntent(action="plant", name="The Broken Seal"),
         ThreadIntent(action="touch", id="nonexistent")],
        active_thread_ids=set(),
    )
    assert len(c.commits) == 1
    name, event_type, agg, payload = c.commits[0]
    assert (name, event_type) == ("author", EventType.THREAD_PLANTED)
    assert payload.id == "the-broken-seal"


@pytest.mark.asyncio
async def test_thread_plant_collision_downgrades_to_touch():
    c = FakeCommitter()
    await commit_thread_intents(
        c, "author",
        [ThreadIntent(action="plant", name="The Broken Seal")],
        active_thread_ids={"the-broken-seal"},
    )
    assert len(c.commits) == 1
    assert c.commits[0][1] == EventType.THREAD_TOUCHED


@pytest.mark.asyncio
async def test_source_is_threaded_through():
    c = FakeCommitter()
    await commit_thread_intents(
        c, "author", [ThreadIntent(action="touch", id="t1")],
        active_thread_ids={"t1"}, source="chat",
    )
    assert c.commits[0][3].source == "chat"


@pytest.mark.asyncio
async def test_knowledge_allowed_actions_restricts():
    c = FakeCommitter()
    await commit_secret_citations(
        c, "character_keeper",
        [SecretCitation(action="reveal", id="s1"),
         SecretCitation(action="learn", id="s1", character_id="c1")],
        active_secret_ids={"s1"},
        allowed_actions=frozenset({"learn"}),
    )
    assert len(c.commits) == 1
    assert c.commits[0][1] == EventType.SECRET_LEARNED


@pytest.mark.asyncio
async def test_theme_introduce_collision_downgrades_to_develop():
    c = FakeCommitter()
    await commit_theme_intents(
        c, "editor", [ThemeIntent(action="introduce", title="Grief")],
        active_theme_ids={"grief"},
    )
    assert len(c.commits) == 1
    assert c.commits[0][1] == EventType.THEME_DEVELOPED


@pytest.mark.asyncio
async def test_causal_drops_self_edge_and_unknown_ids():
    c = FakeCommitter()
    await commit_causal_intents(
        c, "editor",
        [CausalIntent(cause_chapter_id="ch1", effect_chapter_id="ch1"),
         CausalIntent(cause_chapter_id="ch1", effect_chapter_id="chX"),
         CausalIntent(cause_chapter_id="ch1", effect_chapter_id="ch2")],
        valid_chapter_ids={"ch1", "ch2"},
    )
    assert len(c.commits) == 1
    assert c.commits[0][1] == EventType.CAUSAL_EDGE_DECLARED


@pytest.mark.asyncio
async def test_make_mints_slug_and_commits_promise_made():
    c = FakeCommitter()
    await commit_promise_intents(
        c, "author",
        [PromiseIntent(action="make", name="The Sealed Letter", kind="plant", thread_id="t1")],
        active_promise_ids=set(), active_thread_ids={"t1"},
        chapter_id="ch1",
    )
    assert len(c.commits) == 1
    name, event_type, agg, payload = c.commits[0]
    assert (name, event_type) == ("author", EventType.PROMISE_MADE)
    assert payload.id == "the-sealed-letter"
    assert payload.kind == "plant"
    assert payload.thread_id == "t1"
    assert payload.chapter_id == "ch1"


@pytest.mark.asyncio
async def test_make_with_unknown_thread_id_drops_the_link_but_keeps_the_promise():
    c = FakeCommitter()
    await commit_promise_intents(
        c, "author",
        [PromiseIntent(action="make", name="The Sealed Letter", thread_id="ghost")],
        active_promise_ids=set(), active_thread_ids=set(),
    )
    assert len(c.commits) == 1
    assert c.commits[0][1] == EventType.PROMISE_MADE
    assert c.commits[0][3].thread_id == ""


@pytest.mark.asyncio
async def test_make_collision_downgrades_to_progress():
    c = FakeCommitter()
    await commit_promise_intents(
        c, "author",
        [PromiseIntent(action="make", name="The Sealed Letter")],
        active_promise_ids={"the-sealed-letter"}, active_thread_ids=set(),
    )
    assert len(c.commits) == 1
    assert c.commits[0][1] == EventType.PROMISE_PROGRESSED


@pytest.mark.asyncio
async def test_make_with_window_carries_lo_hi_through_to_promise_made():
    c = FakeCommitter()
    await commit_promise_intents(
        c, "author",
        [PromiseIntent(action="make", name="The Sealed Letter", window_lo=5, window_hi=9)],
        active_promise_ids=set(), active_thread_ids=set(),
    )
    assert len(c.commits) == 1
    payload = c.commits[0][3]
    assert payload.window_lo == 5
    assert payload.window_hi == 9


@pytest.mark.asyncio
async def test_make_with_inverted_window_is_committed_zeroed_with_warning(caplog):
    c = FakeCommitter()
    with caplog.at_level("WARNING"):
        await commit_promise_intents(
            c, "author",
            [PromiseIntent(action="make", name="The Sealed Letter", window_lo=9, window_hi=5)],
            active_promise_ids=set(), active_thread_ids=set(),
        )
    assert len(c.commits) == 1
    payload = c.commits[0][3]
    assert payload.window_lo == 0
    assert payload.window_hi == 0
    assert any("invalid window" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_citing_actions_drop_unknown_ids_with_no_commit():
    c = FakeCommitter()
    await commit_promise_intents(
        c, "author",
        [PromiseIntent(action="progress", id="ghost"),
         PromiseIntent(action="pay", id="ghost"),
         PromiseIntent(action="release", id="ghost")],
        active_promise_ids=set(), active_thread_ids=set(),
    )
    assert len(c.commits) == 0


@pytest.mark.asyncio
async def test_pay_and_release_commit_terminal_events():
    c = FakeCommitter()
    await commit_promise_intents(
        c, "author",
        [PromiseIntent(action="pay", id="the-sealed-letter"),
         PromiseIntent(action="release", id="the-sealed-letter", note="red herring")],
        active_promise_ids={"the-sealed-letter"}, active_thread_ids=set(),
    )
    assert len(c.commits) == 2
    assert c.commits[0][1] == EventType.PROMISE_PAID
    assert c.commits[1][1] == EventType.PROMISE_RELEASED
    assert c.commits[1][3].reason == "red herring"


@pytest.mark.asyncio
async def test_blank_name_make_is_dropped():
    c = FakeCommitter()
    await commit_promise_intents(
        c, "author",
        [PromiseIntent(action="make", name="")],
        active_promise_ids=set(), active_thread_ids=set(),
    )
    assert len(c.commits) == 0


# --- blueprint plan ---

@pytest.mark.asyncio
async def test_blueprint_plan_none_is_noop():
    c = FakeCommitter()
    await commit_blueprint_plan(c, "plotter", None)
    assert len(c.commits) == 0


@pytest.mark.asyncio
async def test_blueprint_plan_unknown_framework_dropped(caplog):
    c = FakeCommitter()
    await commit_blueprint_plan(c, "plotter", BlueprintPlan(framework="nonexistent", target_chapter_count=20))
    assert len(c.commits) == 0
    assert any("framework" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_blueprint_plan_tiny_count_dropped(caplog):
    c = FakeCommitter()
    await commit_blueprint_plan(c, "plotter", BlueprintPlan(framework="six-position", target_chapter_count=2))
    assert len(c.commits) == 0
    assert any("target_chapter_count" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_blueprint_plan_happy_path_mints_beats():
    c = FakeCommitter()
    await commit_blueprint_plan(
        c, "plotter", BlueprintPlan(framework="six-position", target_chapter_count=24, genre="mystery")
    )
    assert len(c.commits) == 1
    name, event_type, agg, payload = c.commits[0]
    assert (name, event_type) == ("plotter", EventType.BLUEPRINT_ADOPTED)
    assert payload.framework == "six-position"
    assert payload.genre == "mystery"
    assert len(payload.beats) == len(BEAT_TEMPLATES["six-position"])
    for beat, template in zip(payload.beats, BEAT_TEMPLATES["six-position"]):
        assert beat.beat_id == f"{payload.blueprint_id}-{template.slug}"
        assert beat.slug == template.slug
        assert beat.name == template.name


# --- retarget ---

@pytest.mark.asyncio
async def test_retarget_intent_none_is_noop():
    c = FakeCommitter()
    await commit_retarget_intent(c, "plotter", None, BlueprintRecord(id="b1", framework="six-position", target_chapter_count=20))
    assert len(c.commits) == 0


@pytest.mark.asyncio
async def test_retarget_intent_no_active_blueprint_dropped(caplog):
    c = FakeCommitter()
    await commit_retarget_intent(c, "plotter", RetargetIntent(target_chapter_count=30), None)
    assert len(c.commits) == 0
    assert any("no active blueprint" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_retarget_intent_tiny_count_dropped(caplog):
    c = FakeCommitter()
    blueprint = BlueprintRecord(id="b1", framework="six-position", target_chapter_count=20)
    await commit_retarget_intent(c, "plotter", RetargetIntent(target_chapter_count=2), blueprint)
    assert len(c.commits) == 0
    assert any("target_chapter_count" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_retarget_intent_no_change_dropped(caplog):
    c = FakeCommitter()
    blueprint = BlueprintRecord(id="b1", framework="six-position", target_chapter_count=20)
    await commit_retarget_intent(c, "plotter", RetargetIntent(target_chapter_count=20), blueprint)
    assert len(c.commits) == 0
    assert any("matches the current blueprint" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_retarget_intent_happy_path_commits():
    c = FakeCommitter()
    blueprint = BlueprintRecord(id="b1", framework="six-position", target_chapter_count=20)
    await commit_retarget_intent(c, "plotter", RetargetIntent(target_chapter_count=30, reason="running long"), blueprint)
    assert len(c.commits) == 1
    name, event_type, agg, payload = c.commits[0]
    assert (name, event_type, agg) == ("plotter", EventType.BLUEPRINT_RETARGETED, "b1")
    assert payload.blueprint_id == "b1"
    assert payload.target_chapter_count == 30


# --- brief draft / supersede ---

@pytest.mark.asyncio
async def test_brief_draft_happy_path():
    c = FakeCommitter()
    await commit_brief_intents(
        c, "plotter",
        [BriefIntent(action="draft", target_ordinal=5, goal="raise stakes",
                     threads_to_touch=["t1"], beats_to_hit=["b1"], promises_to_progress=["p1"])],
        open_brief_ids=[], drafted_chapter_count=3,
        active_thread_ids={"t1"}, active_beat_ids={"b1"}, active_promise_ids={"p1"},
    )
    assert len(c.commits) == 1
    name, event_type, agg, payload = c.commits[0]
    assert (name, event_type) == ("plotter", EventType.CHAPTER_BRIEF_DRAFTED)
    assert payload.target_ordinal == 5
    assert payload.goal == "raise stakes"
    assert payload.threads_to_touch == ["t1"]
    assert payload.beats_to_hit == ["b1"]
    assert payload.promises_to_progress == ["p1"]


@pytest.mark.asyncio
async def test_brief_draft_past_ordinal_dropped(caplog):
    c = FakeCommitter()
    await commit_brief_intents(
        c, "plotter",
        [BriefIntent(action="draft", target_ordinal=3, goal="x")],
        open_brief_ids=[], drafted_chapter_count=3,
        active_thread_ids=set(), active_beat_ids=set(), active_promise_ids=set(),
    )
    assert len(c.commits) == 0
    assert any("ordinal" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_brief_draft_blank_goal_dropped(caplog):
    c = FakeCommitter()
    await commit_brief_intents(
        c, "plotter",
        [BriefIntent(action="draft", target_ordinal=5, goal="  ")],
        open_brief_ids=[], drafted_chapter_count=3,
        active_thread_ids=set(), active_beat_ids=set(), active_promise_ids=set(),
    )
    assert len(c.commits) == 0
    assert any("goal" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_brief_draft_filters_unknown_cited_ids_but_keeps_brief(caplog):
    c = FakeCommitter()
    await commit_brief_intents(
        c, "plotter",
        [BriefIntent(action="draft", target_ordinal=5, goal="x",
                     threads_to_touch=["T1 ", "ghost"], beats_to_hit=["ghost-beat"],
                     promises_to_progress=["ghost-promise"])],
        open_brief_ids=[], drafted_chapter_count=3,
        active_thread_ids={"t1"}, active_beat_ids=set(), active_promise_ids=set(),
    )
    assert len(c.commits) == 1
    payload = c.commits[0][3]
    assert payload.threads_to_touch == ["t1"]
    assert payload.beats_to_hit == []
    assert payload.promises_to_progress == []
    assert any("unknown" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_brief_draft_supersedes_existing_open_brief_for_same_ordinal():
    c = FakeCommitter()
    existing = ChapterBriefRecord(id="old-brief", target_ordinal=5, goal="old goal", status=BriefStatus.open)
    await commit_brief_intents(
        c, "plotter",
        [BriefIntent(action="draft", target_ordinal=5, goal="new goal")],
        open_brief_ids=[existing], drafted_chapter_count=3,
        active_thread_ids=set(), active_beat_ids=set(), active_promise_ids=set(),
    )
    assert len(c.commits) == 2
    name0, event_type0, agg0, payload0 = c.commits[0]
    assert event_type0 == EventType.CHAPTER_BRIEF_SUPERSEDED
    assert payload0.brief_id == "old-brief"
    name1, event_type1, agg1, payload1 = c.commits[1]
    assert event_type1 == EventType.CHAPTER_BRIEF_DRAFTED
    assert payload0.superseded_by_brief_id == payload1.brief_id


@pytest.mark.asyncio
async def test_brief_draft_duplicate_ordinal_in_same_batch_supersedes_first():
    c = FakeCommitter()
    await commit_brief_intents(
        c, "plotter",
        [
            BriefIntent(action="draft", target_ordinal=5, goal="first"),
            BriefIntent(action="draft", target_ordinal=5, goal="second"),
        ],
        open_brief_ids=[], drafted_chapter_count=3,
        active_thread_ids=set(), active_beat_ids=set(), active_promise_ids=set(),
    )
    drafted = [entry for entry in c.commits if entry[1] == EventType.CHAPTER_BRIEF_DRAFTED]
    superseded = [entry for entry in c.commits if entry[1] == EventType.CHAPTER_BRIEF_SUPERSEDED]
    assert len(drafted) == 2
    assert len(superseded) == 1
    first_brief_id = drafted[0][3].brief_id
    second_brief_id = drafted[1][3].brief_id
    assert drafted[0][3].goal == "first"
    assert drafted[1][3].goal == "second"
    assert superseded[0][3].brief_id == first_brief_id
    assert superseded[0][3].superseded_by_brief_id == second_brief_id


@pytest.mark.asyncio
async def test_brief_supersede_happy_path():
    c = FakeCommitter()
    await commit_brief_intents(
        c, "plotter",
        [BriefIntent(action="supersede", id="brief-1")],
        open_brief_ids=[ChapterBriefRecord(id="brief-1", target_ordinal=5, goal="g", status=BriefStatus.open)],
        drafted_chapter_count=3,
        active_thread_ids=set(), active_beat_ids=set(), active_promise_ids=set(),
    )
    assert len(c.commits) == 1
    name, event_type, agg, payload = c.commits[0]
    assert event_type == EventType.CHAPTER_BRIEF_SUPERSEDED
    assert payload.brief_id == "brief-1"
    assert payload.superseded_by_brief_id == ""


@pytest.mark.asyncio
async def test_brief_supersede_unknown_id_dropped(caplog):
    c = FakeCommitter()
    await commit_brief_intents(
        c, "plotter",
        [BriefIntent(action="supersede", id="ghost")],
        open_brief_ids=[], drafted_chapter_count=3,
        active_thread_ids=set(), active_beat_ids=set(), active_promise_ids=set(),
    )
    assert len(c.commits) == 0
    assert any("ghost" in r.message for r in caplog.records)


# --- beat fulfill ---

@pytest.mark.asyncio
async def test_beat_fulfill_happy_path():
    c = FakeCommitter()
    await commit_beat_intents(
        c, "plotter",
        [BeatIntent(action="fulfill", beat_id="bp1-midpoint", chapter_id="ch5")],
        active_beat_ids={"bp1-midpoint"}, valid_chapter_ids={"ch5"},
    )
    assert len(c.commits) == 1
    name, event_type, agg, payload = c.commits[0]
    assert (name, event_type) == ("plotter", EventType.BEAT_FULFILLED)
    assert payload.beat_id == "bp1-midpoint"
    assert payload.chapter_id == "ch5"


@pytest.mark.asyncio
async def test_beat_fulfill_unknown_beat_dropped(caplog):
    c = FakeCommitter()
    await commit_beat_intents(
        c, "plotter",
        [BeatIntent(action="fulfill", beat_id="ghost")],
        active_beat_ids=set(), valid_chapter_ids=set(),
    )
    assert len(c.commits) == 0
    assert any("ghost" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_beat_fulfill_unknown_chapter_dropped(caplog):
    c = FakeCommitter()
    await commit_beat_intents(
        c, "plotter",
        [BeatIntent(action="fulfill", beat_id="bp1-midpoint", chapter_id="ghost-chapter")],
        active_beat_ids={"bp1-midpoint"}, valid_chapter_ids=set(),
    )
    assert len(c.commits) == 0
    assert any("ghost-chapter" in r.message for r in caplog.records)


# --- resolution plan ---

@pytest.mark.asyncio
async def test_resolution_plan_happy_path_thread_and_secret():
    c = FakeCommitter()
    await commit_resolution_plan_intents(
        c, "plotter",
        [ResolutionPlanIntent(kind="thread", id="t1", window_lo=3, window_hi=5),
         ResolutionPlanIntent(kind="secret", id="s1", window_lo=6, window_hi=8)],
        active_thread_ids={"t1"}, unrevealed_secret_ids={"s1"},
    )
    assert len(c.commits) == 2
    assert c.commits[0][1] == EventType.THREAD_RESOLUTION_PLANNED
    assert c.commits[0][3].id == "t1"
    assert c.commits[1][1] == EventType.SECRET_REVEAL_PLANNED
    assert c.commits[1][3].id == "s1"


@pytest.mark.asyncio
async def test_resolution_plan_invalid_window_dropped(caplog):
    c = FakeCommitter()
    await commit_resolution_plan_intents(
        c, "plotter",
        [ResolutionPlanIntent(kind="thread", id="t1", window_lo=5, window_hi=2)],
        active_thread_ids={"t1"}, unrevealed_secret_ids=set(),
    )
    assert len(c.commits) == 0
    assert any("window" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_resolution_plan_unknown_id_dropped(caplog):
    c = FakeCommitter()
    await commit_resolution_plan_intents(
        c, "plotter",
        [ResolutionPlanIntent(kind="secret", id="ghost", window_lo=1, window_hi=2)],
        active_thread_ids=set(), unrevealed_secret_ids=set(),
    )
    assert len(c.commits) == 0
    assert any("ghost" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_arc_declare_mints_and_commits():
    c = FakeCommitter()
    await commit_arc_intents(
        c, "character_keeper",
        [ArcIntent(action="declare", character_id="mara", arc_type="positive", lie="I am alone")],
        active_arc_ids=set(), character_ids={"mara"}, active_beat_ids=set(),
    )
    assert len(c.commits) == 1
    name, event_type, agg, payload = c.commits[0]
    assert (name, event_type) == ("character_keeper", EventType.ARC_DECLARED)
    assert payload.character_id == "mara"
    assert payload.arc_type == "positive"
    assert payload.lie == "I am alone"
    assert payload.arc_id == agg


@pytest.mark.asyncio
async def test_arc_declare_unknown_character_dropped(caplog):
    c = FakeCommitter()
    await commit_arc_intents(
        c, "character_keeper",
        [ArcIntent(action="declare", character_id="ghost", arc_type="positive")],
        active_arc_ids=set(), character_ids=set(), active_beat_ids=set(),
    )
    assert len(c.commits) == 0
    assert any("ghost" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_arc_declare_blank_arc_type_dropped(caplog):
    c = FakeCommitter()
    await commit_arc_intents(
        c, "character_keeper",
        [ArcIntent(action="declare", character_id="mara", arc_type="")],
        active_arc_ids=set(), character_ids={"mara"}, active_beat_ids=set(),
    )
    assert len(c.commits) == 0
    assert any("arc_type" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_arc_plan_pivot_requires_active_arc_and_beat():
    c = FakeCommitter()
    await commit_arc_intents(
        c, "character_keeper",
        [ArcIntent(action="plan_pivot", id="arc1", beat_id="beat1", note="turns here")],
        active_arc_ids={"arc1"}, character_ids=set(), active_beat_ids={"beat1"},
    )
    assert len(c.commits) == 1
    name, event_type, agg, payload = c.commits[0]
    assert event_type == EventType.ARC_PIVOT_PLANNED
    assert payload.arc_id == "arc1"
    assert payload.beat_id == "beat1"
    assert payload.description == "turns here"


@pytest.mark.asyncio
async def test_arc_plan_pivot_unknown_arc_dropped(caplog):
    c = FakeCommitter()
    await commit_arc_intents(
        c, "character_keeper",
        [ArcIntent(action="plan_pivot", id="nope", beat_id="beat1")],
        active_arc_ids=set(), character_ids=set(), active_beat_ids={"beat1"},
    )
    assert len(c.commits) == 0
    assert any("nope" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_arc_plan_pivot_unknown_beat_dropped(caplog):
    c = FakeCommitter()
    await commit_arc_intents(
        c, "character_keeper",
        [ArcIntent(action="plan_pivot", id="arc1", beat_id="nope")],
        active_arc_ids={"arc1"}, character_ids=set(), active_beat_ids=set(),
    )
    assert len(c.commits) == 0
    assert any("nope" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_arc_advance_requires_active_arc_and_carries_chapter_id():
    c = FakeCommitter()
    await commit_arc_intents(
        c, "character_keeper",
        [ArcIntent(action="advance", id="arc1", note="took the leap")],
        active_arc_ids={"arc1"}, character_ids=set(), active_beat_ids=set(),
        chapter_id="ch1",
    )
    assert len(c.commits) == 1
    name, event_type, agg, payload = c.commits[0]
    assert event_type == EventType.ARC_ADVANCED
    assert payload.arc_id == "arc1"
    assert payload.chapter_id == "ch1"
    assert payload.note == "took the leap"


@pytest.mark.asyncio
async def test_arc_advance_unknown_arc_dropped(caplog):
    c = FakeCommitter()
    await commit_arc_intents(
        c, "character_keeper",
        [ArcIntent(action="advance", id="nope")],
        active_arc_ids=set(), character_ids=set(), active_beat_ids=set(),
    )
    assert len(c.commits) == 0
    assert any("nope" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_arc_resolve_requires_active_arc_and_outcome():
    c = FakeCommitter()
    await commit_arc_intents(
        c, "character_keeper",
        [ArcIntent(action="resolve", id="arc1", outcome="truth_embraced", note="she sees now")],
        active_arc_ids={"arc1"}, character_ids=set(), active_beat_ids=set(),
        chapter_id="ch2",
    )
    assert len(c.commits) == 1
    name, event_type, agg, payload = c.commits[0]
    assert event_type == EventType.ARC_RESOLVED
    assert payload.arc_id == "arc1"
    assert payload.outcome == "truth_embraced"
    assert payload.chapter_id == "ch2"


@pytest.mark.asyncio
async def test_arc_resolve_blank_outcome_dropped(caplog):
    c = FakeCommitter()
    await commit_arc_intents(
        c, "character_keeper",
        [ArcIntent(action="resolve", id="arc1", outcome="")],
        active_arc_ids={"arc1"}, character_ids=set(), active_beat_ids=set(),
    )
    assert len(c.commits) == 0
    assert any("outcome" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_arc_resolve_unknown_arc_dropped(caplog):
    c = FakeCommitter()
    await commit_arc_intents(
        c, "character_keeper",
        [ArcIntent(action="resolve", id="nope", outcome="truth_embraced")],
        active_arc_ids=set(), character_ids=set(), active_beat_ids=set(),
    )
    assert len(c.commits) == 0
    assert any("nope" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_knowledge_learn_drops_unknown_character_id(caplog):
    """A hallucinated character_id must never reach secret_knowledge: the row
    would put a phantom id in the knowledge matrix's known_by set while the
    real character still reads 'unknown', so the LeakDetector would later
    flag that character's legitimate use as a leak."""
    c = FakeCommitter()
    await commit_secret_citations(
        c, "character_keeper",
        [SecretCitation(action="learn", id="s1", character_id="ghost")],
        active_secret_ids={"s1"}, character_ids={"c1"},
    )
    assert len(c.commits) == 0
    assert any("ghost" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_knowledge_uses_drops_unknown_character_id():
    c = FakeCommitter()
    await commit_secret_citations(
        c, "editor",
        [SecretCitation(action="uses", id="s1", character_id="ghost")],
        active_secret_ids={"s1"}, character_ids={"c1"},
    )
    assert len(c.commits) == 0


@pytest.mark.asyncio
async def test_knowledge_learn_keeps_known_character_id():
    c = FakeCommitter()
    await commit_secret_citations(
        c, "character_keeper",
        [SecretCitation(action="learn", id="s1", character_id="c1")],
        active_secret_ids={"s1"}, character_ids={"c1"},
    )
    assert len(c.commits) == 1
    assert c.commits[0][1] == EventType.SECRET_LEARNED


@pytest.mark.asyncio
async def test_knowledge_character_validation_is_opt_in():
    """character_ids=None means 'caller has no roster to check against' and
    must behave exactly as before -- plant/reveal carry no character_id at
    all, so they are never affected either way."""
    c = FakeCommitter()
    await commit_secret_citations(
        c, "author",
        [SecretCitation(action="learn", id="s1", character_id="whoever")],
        active_secret_ids={"s1"},
    )
    assert len(c.commits) == 1


@pytest.mark.asyncio
async def test_plants_and_reveals_are_unaffected_by_the_character_roster():
    """Neither carries a character_id, so an empty roster must not touch them.
    Plants do not even reach the roster check any more -- commit_secret_plants
    takes no roster argument at all, which is the point of the split."""
    c = FakeCommitter()
    await commit_secret_plants(c, "author", [SecretPlant(title="The Heir Lives")],
                               active_secret_ids={"s1"})
    await commit_secret_citations(
        c, "author", [SecretCitation(action="reveal", id="s1")],
        active_secret_ids={"s1"}, character_ids=set(),
    )
    assert [x[1] for x in c.commits] == [EventType.SECRET_CREATED, EventType.SECRET_REVEALED]


class FakeThemeEmbeddingStore:
    """The minimum embedding-store surface the theme near-duplicate path uses."""

    def __init__(self, near_duplicate_id):
        self._near_duplicate_id = near_duplicate_id
        self.upserted = []

    async def query_themes(self, title, n=1):
        if self._near_duplicate_id is None:
            return []
        return [(self._near_duplicate_id, 0.1)]

    async def upsert_theme(self, theme):
        self.upserted.append(theme)


class HalfReadStore:
    """A read store that answers get_theme but not list_flags -- exactly the
    shape the old getattr guard tolerated on one call and not the other."""

    async def get_theme(self, theme_id):
        return None


@pytest.mark.asyncio
async def test_theme_introduce_never_lands_an_event_whose_similarity_flag_cannot_be_filed(caplog):
    """A theme introduce lands BOTH the theme event and its similarity flag,
    or neither -- never the event alone. A read store that cannot serve the
    flag path used to raise AttributeError only AFTER theme.introduced had
    been committed: the event was permanent, the flag it was supposed to
    arrive with never landed, and the caller saw an exception."""
    c = FakeCommitter()
    embeddings = FakeThemeEmbeddingStore("loss")
    await commit_theme_intents(
        c, "author", [ThemeIntent(action="introduce", title="The Price of Ambition")],
        active_theme_ids={"loss"},
        embedding_store=embeddings, read_store=HalfReadStore(),
    )
    assert [x[1] for x in c.commits] == [EventType.THEME_INTRODUCED]
    # The theme still enters the embedding collection: only the flag-filing
    # half of the similarity path is unavailable, and skipping the upsert too
    # would blind every future duplicate check.
    assert [t.id for t in embeddings.upserted] == ["the-price-of-ambition"]
    assert any("list_flags" in r.message or "similarity" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_theme_introduce_files_the_similarity_flag_with_a_complete_read_store():
    """The happy path is untouched: a read store satisfying the whole
    contract still gets the theme event and its flag, in that order."""
    class FullReadStore(HalfReadStore):
        async def list_flags(self, category=None, status=None, escalated=None):
            return []

    c = FakeCommitter()
    await commit_theme_intents(
        c, "author", [ThemeIntent(action="introduce", title="The Price of Ambition")],
        active_theme_ids={"loss"},
        embedding_store=FakeThemeEmbeddingStore("loss"), read_store=FullReadStore(),
    )
    assert [x[1] for x in c.commits] == [EventType.THEME_INTRODUCED, EventType.FLAG_CREATED]
