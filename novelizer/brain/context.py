from __future__ import annotations
from collections.abc import Mapping
from novelizer.brain.arc_alignment import STAGNATION_CHAPTERS, arc_findings
from novelizer.brain.beat_drift import beat_drifts, next_expected_beat
from novelizer.brain.completion import completion_status
from novelizer.brain.ledger import due_promises, overdue_promises
from novelizer.brain.paradoxes import find_paradoxes
from novelizer.brain.resolution_pacing import congested_windows, overdue_resolutions, overdue_reveals
from novelizer.brain.sag_spike import SAG_SPIKE_DELTA, detect_sag_spike
from novelizer.brain.staleness import STALENESS_THRESHOLD_CHAPTERS, stale_threads
from novelizer.brain.tension_target import tension_deviations
from novelizer.canon.beat_templates import beat_window
from novelizer.canon.secrets import knowledge_cell_state
from novelizer.store.models import (
    ArcRecord, BeatRecord, BlueprintRecord, CausalEdgeRecord, Chapter, Character, PromiseRecord,
    Flag, PromiseState, SecretRecord, StructureScore, ThreadRecord,
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
        # Title first, id in parens: same shape as stale_threads_note. Leading
        # with the id inverts natural-language-first ordering and costs recall.
        lines.append(f"- {secret.title} (id:{secret.id}) — {who}")
    if not lines:
        return ""
    return "\n\nSecrets and who knows them:\n" + "\n".join(lines)


def open_retcons_note(requests: list[Flag]) -> str:
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


def chapter_ordinals(chapter_ids: list[str]) -> dict[str, str]:
    """Map chapter id -> 'chNNN' handle. The ordinal is list position, which is
    what canon_fs.paths.build_path_index uses for the NNN- filename prefix, so a
    chNNN an agent reads here resolves to the file it then opens."""
    return {cid: f"ch{i:03d}" for i, cid in enumerate(chapter_ids, start=1)}


def chapter_map_note(chapters: list[Chapter], gists: Mapping[str, str] | None = None) -> str:
    """Pull-mode chapter index: one line per chapter, never prose.

    Leads with the chNNN ordinal because raw chapter ids are UUIDs, and an
    agent asked to copy a 36-char opaque string back into an intent drops or
    mangles it. The raw id trails in [id:...] for the schemas that still
    require it. A chapter with a Summarizer gist gains an indented gist
    line — what the chapter IS ABOUT — so tooled agents choose what to pull
    from more than a title.
    """
    if not chapters:
        return "None yet."
    ordinal = chapter_ordinals([c.id for c in chapters])
    lines = []
    for c in chapters:
        lines.append(
            f"- {ordinal[c.id]} '{c.title}' ({c.editorial_status.value}) "
            f"cast: {', '.join(c.character_ids) if c.character_ids else 'none'} [id:{c.id}]"
        )
        gist = (gists or {}).get(c.id)
        if gist:
            lines.append(f"    gist: {gist}")
    return "\n".join(lines)


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


def arc_note(
    arcs: list[ArcRecord],
    characters: list[Character],
    chapters: list[Chapter],
    beats: list[BeatRecord],
    blueprint: BlueprintRecord | None,
    stagnation_chapters: int = STAGNATION_CHAPTERS,
) -> str:
    """Build the prompt block naming every arc-alignment finding (a resolved
    arc whose outcome contradicts its type, a stagnant unresolved arc, a
    missed pivot window, or an orphaned pivot citation), with per-character
    guidance: contradictions need adjudication, stagnation/missed pivots
    need routing into the next brief, orphaned pivots need re-pinning to a
    live beat. Empty string when nothing is flagged, so the prompt stays
    byte-identical when quiet.
    """
    findings = arc_findings(arcs, characters, chapters, beats, blueprint, stagnation_chapters)
    if not findings:
        return ""
    names_by_id = {c.id: c.name for c in characters}
    lines = []
    for f in findings:
        name = names_by_id.get(f.character_id, f.character_id)
        if f.kind == "contradiction":
            guidance = f"adjudicate: {f.detail}"
        elif f.kind == "pivot_orphaned":
            guidance = f"re-pin {name}'s pivot — beat {f.beat_id} was superseded"
        else:
            guidance = f"route {name} into the next brief ({f.detail})"
        lines.append(f"- {name} (arc:{f.arc_id}) — {guidance}")
    return "\n\nArc alignment:\n" + "\n".join(lines)


def completion_note(
    blueprint: BlueprintRecord | None,
    beats: list[BeatRecord],
    promises: list[PromiseRecord],
    arcs: list[ArcRecord],
    chapters: list[Chapter],
    characters: list[Character],
) -> str:
    """Build the endgame-steering prompt block for the completion faculty.

    Deliberately quiet until the book is CLOSE: empty string when there is
    no blueprint, or when more than one blocker category remains (this note
    steers the final stretch, not the whole book -- naming every gap from
    chapter one would just be noise). Fires only when exactly one blocker
    category remains, naming it precisely, or when the blueprint is fully
    satisfied.
    """
    status = completion_status(blueprint, beats, promises, arcs, chapters)
    if status is None:
        return ""

    if status.complete:
        return (
            "The blueprint is satisfied: every beat fulfilled, every promise settled, "
            "every arc resolved. Write the ending — then the room is done."
        )

    if len(status.blockers) != 1:
        return ""

    if status.beats_total == 0:
        # "0 beats" is completion_status's furthest-from-done signal (no
        # beats adopted yet), not an endgame near-miss -- naming "0 beats"
        # as the lone remaining blocker would be nonsense.
        return ""

    names_by_id = {c.id: c.name for c in characters}

    if status.beats_fulfilled < status.beats_total:
        unfulfilled = [b for b in beats if not b.fulfilled_by_chapter_id]
        names = ", ".join(b.name for b in unfulfilled)
        count = len(unfulfilled)
        noun = "beat" if count == 1 else "beats"
        return (
            f"Everything is settled except {count} {noun}: {names}. "
            "Steer the remaining chapters at them."
        )

    if status.promises_open:
        open_promises = [p for p in promises if p.state == PromiseState.open]
        names = ", ".join(p.name for p in open_promises)
        count = len(open_promises)
        noun = "promise" if count == 1 else "promises"
        return (
            f"Everything is settled except {count} {noun}: {names}. "
            "Steer the remaining chapters at them."
        )

    unresolved_arcs = [a for a in arcs if a.active and not a.resolved]
    names = ", ".join(names_by_id.get(a.character_id, a.character_id) for a in unresolved_arcs)
    count = len(unresolved_arcs)
    noun = "arc" if count == 1 else "arcs"
    return (
        f"Everything is settled except {count} {noun}: {names}. "
        "Steer the remaining chapters at them."
    )


def _capped_names(names: list[str], cap: int = 3) -> str:
    shown = names[:cap]
    text = ", ".join(shown)
    remainder = len(names) - len(shown)
    if remainder > 0:
        text += f", +{remainder} more"
    return text


def finale_convergence_note(
    blueprint: BlueprintRecord | None,
    beats: list[BeatRecord],
    promises: list[PromiseRecord],
    arcs: list[ArcRecord],
    chapters: list[Chapter],
    characters: list[Character] | None = None,
) -> str:
    """Build the finale-window steering block: empty string until the story
    has entered the finale window, then lists everything that must converge
    before the end (unfulfilled beats, open promises with overdue flagged,
    unresolved active arcs), capped at ~3 names per category, closing with
    how many chapters remain.

    Unresolved arcs are named via `characters` (id -> name), falling back
    to the raw character_id when no matching character is passed.

    Window threshold prefers the climax beat's window_lo (the highest
    ideal_pct beat in the active blueprint's beats, per beat_window),
    falling back to round(0.80 * target_chapter_count) when there are no
    beats. Quiet when nothing remains open -- that's completion_note's job,
    and double-reporting the same "nothing left" state would be noise.
    """
    if blueprint is None:
        return ""

    now = len(chapters)
    target = blueprint.target_chapter_count

    if beats:
        climax = max(beats, key=lambda b: b.ideal_pct)
        window_lo, _ = beat_window(climax.ideal_pct, climax.tolerance_pct, target)
        threshold = window_lo
    else:
        threshold = round(0.80 * target)

    if now < threshold:
        return ""

    unfulfilled = [b for b in beats if not b.fulfilled_by_chapter_id]
    open_promises = [p for p in promises if p.state == PromiseState.open]
    overdue = set(p.id for p in overdue_promises(promises, chapters))
    unresolved_arcs = [a for a in arcs if a.active and not a.resolved]

    if not unfulfilled and not open_promises and not unresolved_arcs:
        return ""

    lines = []
    if unfulfilled:
        names = _capped_names([b.name for b in unfulfilled])
        count = len(unfulfilled)
        noun = "beat" if count == 1 else "beats"
        lines.append(f"- {count} unfulfilled {noun}: {names}")
    if open_promises:
        names = _capped_names(
            [f"{p.name} (OVERDUE)" if p.id in overdue else p.name for p in open_promises]
        )
        count = len(open_promises)
        noun = "promise" if count == 1 else "promises"
        lines.append(f"- {count} open {noun}: {names}")
    if unresolved_arcs:
        names_by_id = {c.id: c.name for c in (characters or [])}
        names = _capped_names(
            [names_by_id.get(a.character_id, a.character_id) for a in unresolved_arcs]
        )
        count = len(unresolved_arcs)
        noun = "arc" if count == 1 else "arcs"
        lines.append(f"- {count} unresolved {noun}: {names}")

    remaining = max(target - now, 0)
    lines.append(
        f"Everything still open must land in the next {remaining} chapters."
    )
    return "\n\nFinale convergence:\n" + "\n".join(lines)


def causal_flags_note(edges: list[CausalEdgeRecord], chapter_order: list[str]) -> str:
    """Build the Editor-facing paradox-candidate summary, calling the *same*
    find_paradoxes function M4.2's Continuity Checker and M4.3's Causeway
    pane use -- no separate paradox logic (M4.3 row). Empty string when
    nothing is flagged.
    """
    candidates = find_paradoxes(edges, chapter_order)
    if not candidates:
        return ""
    ordinal = chapter_ordinals(chapter_order)
    lines = "\n".join(
        f"- {ordinal.get(p.cause_chapter_id, p.cause_chapter_id)} -> "
        f"{ordinal.get(p.effect_chapter_id, p.effect_chapter_id)} ({p.reason})"
        for p in candidates
    )
    return f"\n\nCausal flags:\n{lines}"
