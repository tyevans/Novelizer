def test_arc_pivot_defaults():
    from novelizer.store.models import ArcPivot
    ap = ArcPivot(beat_id="beat1")
    assert ap.description == ""


def test_arc_pivot_round_trip():
    from novelizer.store.models import ArcPivot
    ap = ArcPivot(
        beat_id="beat1",
        description="test description"
    )
    assert ArcPivot.model_validate_json(ap.model_dump_json()) == ap


def test_arc_record_defaults():
    from novelizer.store.models import ArcRecord
    ar = ArcRecord(id="arc1", character_id="char1", arc_type="internal")
    assert ar.ghost == ""
    assert ar.lie == ""
    assert ar.truth == ""
    assert ar.want == ""
    assert ar.need == ""
    assert ar.active is True
    assert ar.resolved is False
    assert ar.outcome == ""
    assert ar.resolved_chapter_id == ""
    assert ar.advance_count == 0
    assert ar.last_note == ""
    assert ar.last_chapter_id == ""
    assert ar.pivots == []


def test_arc_record_round_trip():
    from novelizer.store.models import ArcRecord, ArcPivot
    ar = ArcRecord(
        id="arc1",
        character_id="char1",
        arc_type="internal",
        ghost="ghost value",
        lie="lie value",
        truth="truth value",
        want="want value",
        need="need value",
        active=False,
        resolved=True,
        outcome="success",
        resolved_chapter_id="ch1",
        advance_count=5,
        last_note="test note",
        last_chapter_id="ch2",
        pivots=[
            ArcPivot(beat_id="beat1", description="first pivot"),
            ArcPivot(beat_id="beat2")
        ]
    )
    assert ArcRecord.model_validate_json(ar.model_dump_json()) == ar
