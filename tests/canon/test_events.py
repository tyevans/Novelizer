from novelizer.canon.events import StoredEvent, EventType


def test_event_type_naming_convention():
    for value in [
        EventType.WORLD_ENTRY_CREATED, EventType.CHARACTER_CREATED,
        EventType.CHAPTER_CREATED, EventType.DIRECTOR_SIGNAL_CREATED,
        EventType.DIRECTOR_SIGNAL_CONSUMED,
    ]:
        domain, _, verb = value.partition(".")
        assert domain and verb, f"{value} must be '<domain>.<verb>'"


def test_stored_event_roundtrips_through_json():
    ev = StoredEvent(
        sequence=1, id="abc", event_type=EventType.CHAPTER_CREATED,
        aggregate_id="ch1", payload={"title": "One"}, created_at="2026-07-17T00:00:00Z",
    )
    again = StoredEvent.model_validate_json(ev.model_dump_json())
    assert again == ev


def test_autonomy_and_proposal_event_types_exist():
    from novelizer.canon.events import EventType
    assert EventType.PROPOSAL_CREATED == "proposal.created"
    assert EventType.PROPOSAL_APPROVED == "proposal.approved"
    assert EventType.PROPOSAL_REJECTED == "proposal.rejected"
    assert EventType.AUTONOMY_CHANGED == "autonomy.changed"


def test_agent_remarked_event_type_exists():
    from novelizer.canon.events import EventType
    assert EventType.AGENT_REMARKED == "agent.remarked"


def test_agent_remark_payload_model_roundtrips():
    from novelizer.canon.events import AgentRemark
    remark = AgentRemark(agent_name="author", note="Another storm, another chapter.")
    again = AgentRemark.model_validate_json(remark.model_dump_json())
    assert again == remark


def test_thread_event_types_exist():
    from novelizer.canon.events import EventType
    assert EventType.THREAD_PLANTED == "thread.planted"
    assert EventType.THREAD_TOUCHED == "thread.touched"
    assert EventType.THREAD_PAID_OFF == "thread.paid_off"
    assert EventType.THREAD_ABANDONED == "thread.abandoned"


def test_thread_payload_models_roundtrip():
    from novelizer.canon.events import ThreadPlanted, ThreadTouched, ThreadPaidOff, ThreadAbandoned
    planted = ThreadPlanted(id="the-locket", name="The Locket", chapter_id="c1", note="introduced")
    assert ThreadPlanted.model_validate_json(planted.model_dump_json()) == planted
    for cls in (ThreadTouched, ThreadPaidOff, ThreadAbandoned):
        inst = cls(id="the-locket", chapter_id="c2", note="advanced")
        assert cls.model_validate_json(inst.model_dump_json()) == inst


def test_annotation_structure_scored_event_type_exists():
    from novelizer.canon.events import EventType
    assert EventType.ANNOTATION_STRUCTURE_SCORED == "annotation.structure_scored"


def test_annotation_structure_scored_payload_roundtrips():
    from novelizer.canon.events import AnnotationStructureScored
    scored = AnnotationStructureScored(chapter_id="c1", tension=0.7, pacing_label="rising")
    again = AnnotationStructureScored.model_validate_json(scored.model_dump_json())
    assert again == scored


def test_annotation_structure_scored_tension_is_bounded():
    import pytest
    from pydantic import ValidationError
    from novelizer.canon.events import AnnotationStructureScored
    with pytest.raises(ValidationError):
        AnnotationStructureScored(chapter_id="c1", tension=1.5, pacing_label="off the charts")
    with pytest.raises(ValidationError):
        AnnotationStructureScored(chapter_id="c1", tension=-0.1, pacing_label="negative")


def test_secret_and_causal_edge_event_types_exist():
    from novelizer.canon.events import EventType
    assert EventType.SECRET_CREATED == "secret.created"
    assert EventType.SECRET_LEARNED == "secret.learned"
    assert EventType.SECRET_REFERENCED == "secret.referenced"
    assert EventType.SECRET_REVEALED == "secret.revealed"
    assert EventType.CAUSAL_EDGE_DECLARED == "causal_edge.declared"


def test_secret_payload_models_roundtrip():
    from novelizer.canon.events import (
        SecretCreated, SecretLearned, SecretReferenced, SecretRevealed,
    )
    created = SecretCreated(id="the-heir-lives", title="The Heir Lives", chapter_id="c1", note="planted")
    assert SecretCreated.model_validate_json(created.model_dump_json()) == created
    learned = SecretLearned(id="the-heir-lives", character_id="mara", chapter_id="c2", note="found the letter")
    assert SecretLearned.model_validate_json(learned.model_dump_json()) == learned
    referenced = SecretReferenced(id="the-heir-lives", character_id="mara", chapter_id="c3")
    assert SecretReferenced.model_validate_json(referenced.model_dump_json()) == referenced
    revealed = SecretRevealed(id="the-heir-lives", chapter_id="c4", note="public now")
    assert SecretRevealed.model_validate_json(revealed.model_dump_json()) == revealed


def test_causal_edge_declared_payload_roundtrips():
    from novelizer.canon.events import CausalEdgeDeclared
    edge = CausalEdgeDeclared(cause_chapter_id="c1", effect_chapter_id="c3", note="the fire forces the move")
    assert CausalEdgeDeclared.model_validate_json(edge.model_dump_json()) == edge


def test_promise_event_payloads_construct_with_defaults():
    from novelizer.canon.events import (
        EventType, PromiseMade, PromiseProgressed, PromisePaid, PromiseReleased,
        ThreadResolutionPlanned, SecretRevealPlanned,
    )
    made = PromiseMade(id="the-sealed-letter", name="The Sealed Letter")
    assert made.kind == "foreshadow" and made.window_lo == 0 and made.window_hi == 0
    assert PromiseProgressed(id="x").note == ""
    assert PromisePaid(id="x").chapter_id == ""
    assert PromiseReleased(id="x").reason == ""
    trp = ThreadResolutionPlanned(id="t", window_lo=18, window_hi=20)
    assert trp.planned_payoff_note == ""
    srp = SecretRevealPlanned(id="s", window_lo=5, window_hi=9)
    assert srp.window_hi == 9
    assert EventType.PROMISE_MADE == "promise.made"
    assert EventType.THREAD_RESOLUTION_PLANNED == "thread.resolution_planned"
    assert EventType.SECRET_REVEAL_PLANNED == "secret.reveal_planned"


def test_blueprint_beat_brief_event_payloads_construct_with_defaults():
    from novelizer.canon.events import (
        EventType, BeatSpec, BlueprintAdopted, BlueprintRetargeted,
        BeatFulfilled, ChapterBriefDrafted, ChapterBriefSuperseded,
        ChapterBriefFulfilled,
    )
    # Test BeatSpec defaults
    bs = BeatSpec(beat_id="b-intro", slug="intro", name="Intro", ideal_pct=0.05, tolerance_pct=0.02)
    assert bs.expected_polarity == ""

    # Test BlueprintAdopted defaults
    adopted = BlueprintAdopted(blueprint_id="bp1", framework="six-position", target_chapter_count=24)
    assert adopted.genre == ""
    assert adopted.beats == []
    assert adopted.obligatory_scenes == []
    assert adopted.note == ""

    # Test BlueprintRetargeted
    retargeted = BlueprintRetargeted(blueprint_id="bp1", target_chapter_count=26)

    # Test BeatFulfilled defaults
    bf = BeatFulfilled(beat_id="b-intro")
    assert bf.chapter_id == ""
    assert bf.note == ""

    # Test ChapterBriefDrafted defaults
    cbd = ChapterBriefDrafted(brief_id="br1", target_ordinal=3, goal="save the city")
    assert cbd.pov_character_id == ""
    assert cbd.threads_to_touch == []
    assert cbd.beats_to_hit == []
    assert cbd.promises_to_progress == []
    assert cbd.value_shift == ""
    assert cbd.planned_outcome == ""
    assert cbd.synopsis == ""

    # Test ChapterBriefSuperseded defaults
    cbs = ChapterBriefSuperseded(brief_id="br1")
    assert cbs.superseded_by_brief_id == ""

    # Test ChapterBriefFulfilled
    cbf = ChapterBriefFulfilled(brief_id="br1", chapter_id="ch3")

    # Test constants
    assert EventType.BLUEPRINT_ADOPTED == "blueprint.adopted"
    assert EventType.BLUEPRINT_RETARGETED == "blueprint.retargeted"
    assert EventType.BEAT_FULFILLED == "beat.fulfilled"
    assert EventType.CHAPTER_BRIEF_DRAFTED == "chapter_brief.drafted"
    assert EventType.CHAPTER_BRIEF_SUPERSEDED == "chapter_brief.superseded"
    assert EventType.CHAPTER_BRIEF_FULFILLED == "chapter_brief.fulfilled"
