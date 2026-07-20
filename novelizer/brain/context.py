from __future__ import annotations
from novelizer.brain.beat_drift import beat_drifts, next_expected_beat
from novelizer.brain.ledger import due_promises, overdue_promises
from novelizer.brain.paradoxes import find_paradoxes
from novelizer.brain.resolution_pacing import congested_windows, overdue_resolutions, overdue_reveals
from novelizer.brain.sag_spike import SAG_SPIKE_DELTA, detect_sag_spike
from novelizer.brain.staleness import STALENESS_THRESHOLD_CHAPTERS, stale_threads
from novelizer.brain.tension_target import tension_deviations
from novelizer.canon.secrets import knowledge_cell_state
from novelizer.store.models import (
    BeatRecord, BlueprintRecord, CausalEdgeRecord, Chapter, Character, PromiseRecord, RetconRequest,
    SecretRecord, StructureScore, ThreadRecord,
)


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


def open_retcons_note(requests: list[RetconRequest]) -> str:
    """Build the checker-facing prompt block listing retcon requests already
    sitting open in the queue, so an LLM pass that re-reviews the same
    material every cycle doesn't re-report a known issue under fresh wording
    (exact-description dedup at commit time can't catch a reworded repeat).
    Empty string when the queue is empty, so prompts stay byte-identical
    whenever there is nothing to say.
    """
    if not requests:
        return ""
    lines = "\n".join(f"- {r.description}" for r in requests[:20])
    return f"\n\nRetcon requests already filed (do not re-report these):\n{lines}"


def chapter_map_note(chapters: list[Chapter]) -> str:
    """Pull-mode chapter index: one line per chapter, never prose."""
    if not chapters:
        return "None yet."
    return "\n".join(
        f"- [{c.id}] '{c.title}' ({c.editorial_status.value}) "
        f"cast: {', '.join(c.character_ids) if c.character_ids else 'none'}"
        for c in chapters
    )


def ledger_note(promises: list[PromiseRecord], chapters: list[Chapter]) -> str:
    """Build the Author-facing prompt block naming every promise the ledger
    considers overdue or due, with the exact id the Author must cite to pay
    or release it. Overdue promises are listed first. Empty string when
    neither list has anything, so the prompt stays byte-identical whenever
    the ledger has nothing to say.
    """
    overdue = overdue_promises(promises, chapters)
    due = due_promises(promises, chapters)
    if not overdue and not due:
        return ""
    lines = [f"- {p.name} (id:{p.id}) — OVERDUE — window closed ch {p.window_hi}" for p in overdue]
    lines += [f"- {p.name} (id:{p.id}) — due ch {p.window_lo}-{p.window_hi}" for p in due]
    return "\n\nPromise ledger (pay or release these, citing ids exactly):\n" + "\n".join(lines)


def resolution_pacing_note(
    threads: list[ThreadRecord], secrets: list[SecretRecord], chapters: list[Chapter],
) -> str:
    """Build the prompt block naming overdue thread resolutions, overdue
    secret reveals, and congestion warnings for windows carrying too many
    resolutions at once. Empty string when the pacing faculty has nothing
    to flag, so the prompt stays byte-identical when quiet.
    """
    overdue_threads = overdue_resolutions(threads, chapters)
    overdue_secrets = overdue_reveals(secrets, chapters)
    spans = congested_windows(threads, secrets)
    if not overdue_threads and not overdue_secrets and not spans:
        return ""
    lines = [f"- {t.name} (id:{t.id}) — OVERDUE — window closed ch {t.window_hi}" for t in overdue_threads]
    lines += [
        f"- {s.title} (id:{s.id}) — reveal OVERDUE — window closed ch {s.reveal_window_hi}"
        for s in overdue_secrets
    ]
    lines += [
        f"- {count} resolutions must resolve in the same window (ch {lo}-{hi})"
        for lo, hi, count in spans
    ]
    return "\n\nResolution pacing:\n" + "\n".join(lines)


def beat_drift_note(
    blueprint: BlueprintRecord | None, beats: list[BeatRecord], chapters: list[Chapter],
) -> str:
    """Build the prompt block naming every beat currently drifting from its
    ideal window (early, late, or off-window per beat_drifts). Empty string
    when nothing has drifted, so the prompt stays byte-identical when quiet.
    """
    drifts = beat_drifts(blueprint, beats, chapters)
    if not drifts:
        return ""
    lines = "\n".join(f"- {d.detail}" for d in drifts)
    return f"\n\nBeat drift:\n{lines}"


def tension_target_note(
    blueprint: BlueprintRecord | None,
    beats: list[BeatRecord],
    scores: list[StructureScore],
    chapters: list[Chapter],
) -> str:
    """Build the single-sentence prompt block naming the worst tension
    deviation from the blueprint's target curve, plus the polarity guidance
    for whichever beat is next expected -- deliberately terse (one sentence,
    not a list) since this note steers re-planning, not enumeration. Empty
    string when there are no deviations, so the prompt stays byte-identical
    when quiet.
    """
    from novelizer.canon.beat_templates import beat_window

    deviations = tension_deviations(blueprint, beats, scores, chapters)
    if not deviations:
        return ""
    chapter_id, actual, target = max(deviations, key=lambda d: abs(d[1] - d[2]))
    ordinal = next((i + 1 for i, c in enumerate(chapters) if c.id == chapter_id), None)
    label = f"ch {ordinal}" if ordinal is not None else chapter_id
    sentence = f"Tension vs blueprint: {label} actual {actual:.2g} vs target {target:.2g}"
    next_beat = next_expected_beat(blueprint, beats, chapters)
    if next_beat is not None and blueprint is not None:
        lo, hi = beat_window(next_beat.ideal_pct, next_beat.tolerance_pct, blueprint.target_chapter_count)
        guidance = (
            f"{next_beat.name.lower()} {next_beat.expected_polarity}".strip()
            if next_beat.expected_polarity
            else next_beat.name.lower()
        )
        sentence += f" — the {guidance} is planned for ch {lo}-{hi}."
    else:
        sentence += "."
    return f"\n\n{sentence}"


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
