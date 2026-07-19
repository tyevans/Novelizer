from __future__ import annotations
from novelizer.brain.paradoxes import find_paradoxes
from novelizer.brain.sag_spike import SAG_SPIKE_DELTA, detect_sag_spike
from novelizer.brain.staleness import STALENESS_THRESHOLD_CHAPTERS, stale_threads
from novelizer.canon.secrets import knowledge_cell_state
from novelizer.store.models import CausalEdgeRecord, Chapter, Character, SecretRecord, StructureScore, ThreadRecord


def stale_threads_note(
    threads: list[ThreadRecord],
    chapters: list[Chapter],
    threshold: int = STALENESS_THRESHOLD_CHAPTERS,
) -> str:
    """Build the Author-facing prompt block naming every currently-stale
    thread and the id the Author must cite to touch it back (per M3.1's
    thread identity rule -- ids are never invented, only cited). Empty
    string when nothing is stale, so Author.work()'s prompt stays
    byte-identical to pre-M3.3 output whenever the brain has nothing to say.
    """
    stale = stale_threads(threads, chapters, threshold)
    if not stale:
        return ""
    lines = "\n".join(f"- {t.name} (id:{t.id})" for t in stale)
    return f"\n\nStale threads (consider touching one, citing its id exactly):\n{lines}"


def pacing_flags_note(scores: list[StructureScore], delta: float = SAG_SPIKE_DELTA) -> str:
    """Build the Editor-facing prompt block naming every chapter the pure
    sag/spike detector has flagged. Empty string when nothing is flagged.
    """
    flags = detect_sag_spike(scores, delta)
    if not flags:
        return ""
    lines = "\n".join(f"- chapter {chapter_id}: {flag}" for chapter_id, flag in flags.items())
    return f"\n\nPacing flags:\n{lines}"


def known_secrets_note(
    secrets: list[SecretRecord], characters: list[Character], matrix: dict[str, dict]
) -> str:
    """Build the Author-facing who-knows-what summary of every non-revealed
    secret and which characters currently know it (M4 Locked decision #7).

    Deliberately NOT POV-scoped: injected before the chapter exists, when no
    POV has been chosen (and no POV field exists in the chapter schema), so
    the only coherent form is the full summary -- it equips the Author to
    avoid a leak for *any* character it chooses to write. Revealed secrets
    are omitted (they can no longer leak). Empty string when there are no
    non-revealed secrets, so Author.work()'s prompt stays byte-identical to
    pre-M4.3 output whenever the brain has nothing to say.
    """
    names_by_id = {c.id: c.name for c in characters}
    lines = []
    for secret in secrets:
        if secret.revealed:
            continue
        known = sorted(
            names_by_id.get(cid, cid)
            for cid in names_by_id
            if knowledge_cell_state(matrix, secret.id, cid) == "known"
        )
        who = f"known only to {', '.join(known)}" if known else "known to no one"
        lines.append(f"- '{secret.id}' ({secret.title}) — {who}")
    if not lines:
        return ""
    return "\n\nSecrets and who knows them:\n" + "\n".join(lines)


def causal_flags_note(edges: list[CausalEdgeRecord], chapter_order: list[str]) -> str:
    """Build the Editor-facing paradox-candidate summary, calling the *same*
    find_paradoxes function M4.2's Continuity Checker and M4.3's Causeway
    pane use -- no separate paradox logic (M4.3 row). Empty string when
    nothing is flagged.
    """
    candidates = find_paradoxes(edges, chapter_order)
    if not candidates:
        return ""
    lines = "\n".join(
        f"- chapter {p.cause_chapter_id} -> chapter {p.effect_chapter_id} ({p.reason})" for p in candidates
    )
    return f"\n\nCausal flags:\n{lines}"
