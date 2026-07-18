from __future__ import annotations
from pydantic import BaseModel
from novelizer.canon.secrets import knowledge_cell_state
from novelizer.store.models import SecretReferenceRecord

LEAK_SOURCE_TAG = "[source: leak_detector]"


class Leak(BaseModel):
    """A committed secret.referenced event with no covering learn/reveal.

    Never persisted -- computed fresh from ReadStore data every time
    find_leaks runs (novelizer/agents/continuity_checker.py, and later
    M4.3's Who-Knows-What render helper), same precedent as
    novelizer/brain/staleness.py's is_thread_stale.
    """

    secret_id: str
    character_id: str
    chapter_id: str
    note: str = ""


def find_leaks(references: list[SecretReferenceRecord], matrix: dict[str, dict]) -> list[Leak]:
    """A reference is a leak iff knowledge_cell_state(matrix, secret_id,
    character_id) == "unknown" -- the character has neither learned the
    secret nor is it revealed, per the current knowledge matrix (see this
    module's Decision Note in the M4.2 plan for why the current aggregate
    matrix, not a chapter-H-bounded historical snapshot, is the input
    shape). References are never deduped (M4.1 Locked decision #3) --
    every leaking reference is reported, preserving input order.
    """
    return [
        Leak(secret_id=ref.secret_id, character_id=ref.character_id, chapter_id=ref.chapter_id, note=ref.note)
        for ref in references
        if knowledge_cell_state(matrix, ref.secret_id, ref.character_id) == "unknown"
    ]


def leak_description(leak: Leak) -> str:
    """The single place a leak's retcon-request description is formatted.
    Deterministic given the same (secret_id, character_id, chapter_id) --
    novelizer/agents/continuity_checker.py's dedup check relies on this to
    recognize "the same leak" across polling cycles without any new
    persisted state.
    """
    return (
        f"{LEAK_SOURCE_TAG} secret '{leak.secret_id}' is referenced by character "
        f"'{leak.character_id}' in chapter '{leak.chapter_id}' with no prior learn or reveal."
    )
