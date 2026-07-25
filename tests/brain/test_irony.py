from hypothesis import given, settings, strategies as st

from novelizer.brain.irony import (
    NOT_ON_PAGE_NOTE, NO_GAP_NOTE, REVEALED_OFF_PAGE_NOTE, UNANCHORED_NOTE,
    IronyGap, build_irony_ledger, chapter_ordinals,
)
from novelizer.store.models import (
    Chapter, SecretKnowledgeRecord, SecretRecord, SecretReferenceRecord,
)


def ch(chapter_id: str, *character_ids: str) -> Chapter:
    return Chapter(
        id=chapter_id, title=chapter_id, prose="x", character_ids=list(character_ids)
    )


def matrix_of(*, revealed: bool = False, known_by: set[str] | None = None) -> dict:
    return {"revealed": revealed, "known_by": known_by or set()}


# -- chapter ordering ------------------------------------------------------

def test_chapter_ordinals_are_list_position_not_id_order():
    chapters = [ch("zeta"), ch("alpha"), ch("mid")]
    assert chapter_ordinals(chapters) == {"zeta": 1, "alpha": 2, "mid": 3}


def test_chapter_ordinals_of_an_empty_story_is_empty():
    assert chapter_ordinals([]) == {}


# -- reader onset ----------------------------------------------------------

def test_reader_onset_is_the_first_referencing_chapter_in_story_order():
    chapters = [ch("c1"), ch("c2"), ch("c3")]
    entries = build_irony_ledger(
        secrets=[SecretRecord(id="s1", title="The heir lives")],
        references=[
            SecretReferenceRecord(secret_id="s1", character_id="mara", chapter_id="c3"),
            SecretReferenceRecord(secret_id="s1", character_id="mara", chapter_id="c2"),
        ],
        knowledge=[],
        chapters=chapters,
        matrix={"s1": matrix_of()},
    )
    assert len(entries) == 1
    assert entries[0].reader_from_ordinal == 2
    assert entries[0].reader_from_chapter_id == "c2"


def test_a_secret_that_merely_exists_is_not_reader_knowledge():
    entries = build_irony_ledger(
        secrets=[SecretRecord(id="s1", title="Unspoken")],
        references=[], knowledge=[], chapters=[ch("c1")], matrix={"s1": matrix_of()},
    )
    assert entries[0].reader_from_ordinal is None
    assert entries[0].gaps == []
    assert entries[0].note == NOT_ON_PAGE_NOTE


def test_revealed_but_never_referenced_says_so_rather_than_claiming_a_gap():
    entries = build_irony_ledger(
        secrets=[SecretRecord(id="s1", title="Public now", revealed=True)],
        references=[], knowledge=[], chapters=[ch("c1", "mara")],
        matrix={"s1": matrix_of(revealed=True)},
    )
    assert entries[0].reader_from_ordinal is None
    assert entries[0].gaps == []
    assert entries[0].note == REVEALED_OFF_PAGE_NOTE


def test_references_with_no_chapter_on_record_cannot_be_placed():
    entries = build_irony_ledger(
        secrets=[SecretRecord(id="s1", title="Floating")],
        references=[SecretReferenceRecord(secret_id="s1", character_id="mara", chapter_id="")],
        knowledge=[], chapters=[ch("c1", "mara")], matrix={"s1": matrix_of()},
    )
    assert entries[0].reader_from_ordinal is None
    assert entries[0].note == UNANCHORED_NOTE


# -- gaps ------------------------------------------------------------------

def test_a_character_who_learns_later_has_a_closed_gap():
    chapters = [ch("c1"), ch("c2"), ch("c3", "tomas"), ch("c4", "tomas"), ch("c5")]
    entries = build_irony_ledger(
        secrets=[SecretRecord(id="s1", title="The heir lives")],
        references=[SecretReferenceRecord(secret_id="s1", character_id="mara", chapter_id="c2")],
        knowledge=[SecretKnowledgeRecord(secret_id="s1", character_id="tomas", chapter_id="c5")],
        chapters=chapters,
        matrix={"s1": matrix_of(known_by={"tomas"})},
    )
    assert entries[0].gaps == [IronyGap(
        secret_id="s1", character_id="tomas",
        reader_from_ordinal=2, character_from_ordinal=5,
        closed_by="learned", length=3, live_chapters=[3, 4],
    )]


def test_a_character_who_never_learns_has_an_open_gap_through_the_drafted_story():
    chapters = [ch("c1"), ch("c2", "mara"), ch("c3", "mara")]
    entries = build_irony_ledger(
        secrets=[SecretRecord(id="s1", title="The heir lives")],
        references=[SecretReferenceRecord(secret_id="s1", character_id="ren", chapter_id="c2")],
        knowledge=[], chapters=chapters, matrix={"s1": matrix_of()},
    )
    gap = entries[0].gaps[0]
    assert gap.character_id == "mara"
    assert gap.closed_by == "open"
    assert gap.character_from_ordinal is None
    assert gap.length == 2  # chapters 2 and 3
    assert gap.live_chapters == [2, 3]


def test_a_character_who_knew_before_the_reader_is_no_irony():
    chapters = [ch("c1", "tomas"), ch("c2", "tomas")]
    entries = build_irony_ledger(
        secrets=[SecretRecord(id="s1", title="The heir lives")],
        references=[SecretReferenceRecord(secret_id="s1", character_id="tomas", chapter_id="c2")],
        knowledge=[SecretKnowledgeRecord(secret_id="s1", character_id="tomas", chapter_id="c1")],
        chapters=chapters, matrix={"s1": matrix_of(known_by={"tomas"})},
    )
    assert entries[0].gaps == []
    assert entries[0].note == NO_GAP_NOTE


def test_a_character_never_on_page_while_in_the_dark_yields_no_gap():
    # eda appears only before the reader learns, so no scene can play the irony
    chapters = [ch("c1", "eda"), ch("c2", "mara")]
    entries = build_irony_ledger(
        secrets=[SecretRecord(id="s1", title="The heir lives")],
        references=[SecretReferenceRecord(secret_id="s1", character_id="mara", chapter_id="c2")],
        knowledge=[], chapters=chapters, matrix={"s1": matrix_of()},
    )
    assert [g.character_id for g in entries[0].gaps] == ["mara"]


def test_a_revealed_secret_closes_the_gap_but_the_reveal_chapter_is_unmeasurable():
    chapters = [ch("c1"), ch("c2", "mara"), ch("c3", "mara")]
    entries = build_irony_ledger(
        secrets=[SecretRecord(id="s1", title="The heir lives", revealed=True)],
        references=[SecretReferenceRecord(secret_id="s1", character_id="mara", chapter_id="c2")],
        knowledge=[], chapters=chapters, matrix={"s1": matrix_of(revealed=True)},
    )
    gap = entries[0].gaps[0]
    assert gap.closed_by == "reveal"
    assert gap.character_from_ordinal is None
    assert gap.length is None
    assert gap.live_chapters == [2, 3]


# -- ordering --------------------------------------------------------------

def test_entries_are_ordered_by_reader_onset_with_unplaced_secrets_last():
    chapters = [ch("c1", "mara"), ch("c2", "mara"), ch("c3", "mara")]
    entries = build_irony_ledger(
        secrets=[
            SecretRecord(id="late", title="Late"),
            SecretRecord(id="never", title="Never"),
            SecretRecord(id="early", title="Early"),
        ],
        references=[
            SecretReferenceRecord(secret_id="late", character_id="mara", chapter_id="c3"),
            SecretReferenceRecord(secret_id="early", character_id="mara", chapter_id="c1"),
        ],
        knowledge=[], chapters=chapters,
        matrix={"late": matrix_of(), "never": matrix_of(), "early": matrix_of()},
    )
    assert [e.secret_id for e in entries] == ["early", "late", "never"]


def test_gaps_are_ordered_by_first_live_chapter_then_character_id():
    chapters = [ch("c1", "mara"), ch("c2", "abe", "mara"), ch("c3", "zed")]
    entries = build_irony_ledger(
        secrets=[SecretRecord(id="s1", title="S")],
        references=[SecretReferenceRecord(secret_id="s1", character_id="x", chapter_id="c1")],
        knowledge=[], chapters=chapters, matrix={"s1": matrix_of()},
    )
    assert [g.character_id for g in entries[0].gaps] == ["mara", "abe", "zed"]


# -- invariants ------------------------------------------------------------

def test_every_secret_appears_exactly_once_even_with_many_references():
    chapters = [ch("c1", "mara"), ch("c2", "mara")]
    refs = [
        SecretReferenceRecord(secret_id="s1", character_id="mara", chapter_id="c1")
        for _ in range(5)
    ]
    entries = build_irony_ledger(
        secrets=[SecretRecord(id="s1", title="S")], references=refs,
        knowledge=[], chapters=chapters, matrix={"s1": matrix_of()},
    )
    assert len(entries) == 1


def test_a_secret_absent_from_the_matrix_still_gets_an_entry():
    entries = build_irony_ledger(
        secrets=[SecretRecord(id="s1", title="S")], references=[], knowledge=[],
        chapters=[ch("c1")], matrix={},
    )
    assert [e.secret_id for e in entries] == ["s1"]


def test_no_secrets_means_an_empty_ledger():
    assert build_irony_ledger(
        secrets=[], references=[], knowledge=[], chapters=[ch("c1")], matrix={}
    ) == []


_ids = st.text(alphabet="abcde", min_size=1, max_size=3)


@given(
    secret_ids=st.lists(_ids, max_size=4, unique=True),
    chapter_ids=st.lists(_ids, max_size=5, unique=True),
    cast=st.lists(_ids, max_size=3, unique=True),
    revealed=st.booleans(),
)
@settings(max_examples=150)
def test_ledger_is_one_entry_per_secret_and_gaps_never_precede_the_reader(
    secret_ids, chapter_ids, cast, revealed
):
    chapters = [ch(cid, *cast) for cid in chapter_ids]
    secrets = [SecretRecord(id=s, title=s, revealed=revealed) for s in secret_ids]
    references = [
        SecretReferenceRecord(secret_id=s, character_id=c, chapter_id=cid)
        for s in secret_ids for c in cast for cid in chapter_ids[:1]
    ]
    matrix = {s: matrix_of(revealed=revealed) for s in secret_ids}
    entries = build_irony_ledger(
        secrets=secrets, references=references, knowledge=[],
        chapters=chapters, matrix=matrix,
    )
    assert [e.secret_id for e in entries] == sorted(secret_ids) or len(entries) == len(secret_ids)
    assert len(entries) == len(secret_ids)
    for entry in entries:
        for gap in entry.gaps:
            assert gap.reader_from_ordinal == entry.reader_from_ordinal
            assert all(o >= gap.reader_from_ordinal for o in gap.live_chapters)
            if gap.character_from_ordinal is not None:
                assert gap.character_from_ordinal > gap.reader_from_ordinal
