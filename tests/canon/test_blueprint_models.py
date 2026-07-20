def test_brief_status_enum():
    from novelizer.store.models import BriefStatus
    assert BriefStatus.open == "open"
    assert BriefStatus.superseded == "superseded"
    assert BriefStatus.fulfilled == "fulfilled"


def test_beat_record_defaults():
    from novelizer.store.models import BeatRecord
    b = BeatRecord(id="b1", blueprint_id="bp1", slug="test-beat", name="Test Beat", ideal_pct=0.5, tolerance_pct=0.1)
    assert b.expected_polarity == ""
    assert b.fulfilled_by_chapter_id == ""
    assert b.note == ""


def test_beat_record_round_trip():
    from novelizer.store.models import BeatRecord
    b = BeatRecord(
        id="b1",
        blueprint_id="bp1",
        slug="test-beat",
        name="Test Beat",
        ideal_pct=0.5,
        tolerance_pct=0.1,
        expected_polarity="positive",
        fulfilled_by_chapter_id="ch1",
        note="test note"
    )
    assert BeatRecord.model_validate_json(b.model_dump_json()) == b


def test_blueprint_record_defaults():
    from novelizer.store.models import BlueprintRecord
    bp = BlueprintRecord(id="bp1", framework="three-act", target_chapter_count=10)
    assert bp.genre == ""
    assert bp.obligatory_scenes == []
    assert bp.active is True
    assert bp.note == ""


def test_blueprint_record_round_trip():
    from novelizer.store.models import BlueprintRecord
    bp = BlueprintRecord(
        id="bp1",
        framework="three-act",
        target_chapter_count=10,
        genre="fantasy",
        obligatory_scenes=["scene1", "scene2"],
        active=False,
        note="test note"
    )
    assert BlueprintRecord.model_validate_json(bp.model_dump_json()) == bp


def test_chapter_brief_record_defaults():
    from novelizer.store.models import ChapterBriefRecord, BriefStatus
    cb = ChapterBriefRecord(id="cb1", target_ordinal=1, goal="test goal")
    assert cb.pov_character_id == ""
    assert cb.threads_to_touch == []
    assert cb.beats_to_hit == []
    assert cb.promises_to_progress == []
    assert cb.value_shift == ""
    assert cb.planned_outcome == ""
    assert cb.synopsis == ""
    assert cb.status == BriefStatus.open
    assert cb.superseded_by_brief_id == ""
    assert cb.fulfilled_by_chapter_id == ""


def test_chapter_brief_record_round_trip():
    from novelizer.store.models import ChapterBriefRecord, BriefStatus
    cb = ChapterBriefRecord(
        id="cb1",
        target_ordinal=1,
        goal="test goal",
        pov_character_id="char1",
        threads_to_touch=["t1", "t2"],
        beats_to_hit=["b1"],
        promises_to_progress=["p1"],
        value_shift="positive",
        planned_outcome="success",
        synopsis="test synopsis",
        status=BriefStatus.fulfilled,
        superseded_by_brief_id="cb2",
        fulfilled_by_chapter_id="ch1"
    )
    assert ChapterBriefRecord.model_validate_json(cb.model_dump_json()) == cb
