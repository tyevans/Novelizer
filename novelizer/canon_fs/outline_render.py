from __future__ import annotations

from novelizer.brain.beat_drift import beat_drifts
from novelizer.brain.ledger import overdue_promises
from novelizer.canon.beat_templates import beat_window
from novelizer.canon.threads import TERMINAL_STATES as TERMINAL_THREAD_STATES
from novelizer.store.models import (
    BeatRecord, BlueprintRecord, Chapter, ChapterBriefRecord, PromiseRecord,
    PromiseState, ThreadRecord,
)

NO_BLUEPRINT_BODY = "No blueprint adopted."


# Mirrors novelizer/canon_fs/render.py's private `_frontmatter` helper.
# Kept as a local copy (that one is underscore-private to its module and not
# meant for cross-module import) rather than importing a private symbol.
def _frontmatter(pairs: list[tuple[str, str]]) -> str:
    sanitized = [(k, " ".join(str(v).split())) for k, v in pairs]
    lines = "\n".join(f"{k}: {v}" for k, v in sanitized if v != "")
    return f"---\n{lines}\n---\n"


def render_blueprint(blueprint: BlueprintRecord | None, beats: list[BeatRecord]) -> str:
    if blueprint is None:
        fm = _frontmatter([("kind", "blueprint")])
        return f"{fm}\n{NO_BLUEPRINT_BODY}\n"

    fm = _frontmatter([
        ("id", blueprint.id),
        ("kind", "blueprint"),
        ("framework", blueprint.framework),
        ("target_chapter_count", str(blueprint.target_chapter_count)),
    ])
    body = [f"\n# {blueprint.framework}\n"]
    if blueprint.genre:
        body.append(f"\nGenre: {blueprint.genre}\n")
    if blueprint.obligatory_scenes:
        scenes = "\n".join(f"- {s}" for s in blueprint.obligatory_scenes)
        body.append(f"\n## Obligatory scenes\n\n{scenes}\n")
    if blueprint.note:
        body.append(f"\n{blueprint.note}\n")

    lines = ["\n## Beats\n"]
    for beat in beats:
        window_lo, window_hi = beat_window(
            beat.ideal_pct, beat.tolerance_pct, blueprint.target_chapter_count
        )
        status = "fulfilled" if beat.fulfilled_by_chapter_id else "pending"
        lines.append(
            f"- {beat.name} (window {window_lo}-{window_hi}, {status})"
        )
    body.append("\n".join(lines) + "\n")

    return fm + "".join(body)


def render_beats(
    blueprint: BlueprintRecord | None,
    beats: list[BeatRecord],
    chapters: list[Chapter],
) -> str:
    fm = _frontmatter([("kind", "beats")])
    if blueprint is None:
        return f"{fm}\n{NO_BLUEPRINT_BODY}\n"

    drifts_by_id = {d.beat_id: d for d in beat_drifts(blueprint, beats, chapters)}
    lines = ["\n# Beats\n"]
    for beat in beats:
        window_lo, window_hi = beat_window(
            beat.ideal_pct, beat.tolerance_pct, blueprint.target_chapter_count
        )
        polarity = beat.expected_polarity or "—"
        fulfilled_by = beat.fulfilled_by_chapter_id or "—"
        line = (
            f"- {beat.name}: window {window_lo}-{window_hi}, polarity {polarity}, "
            f"fulfilled_by {fulfilled_by}"
        )
        drift = drifts_by_id.get(beat.id)
        if drift is not None:
            line += f" [{drift.kind}: {drift.detail}]"
        lines.append(line)
    return fm + "\n".join(lines) + "\n"


def render_brief(brief: ChapterBriefRecord) -> str:
    fm = _frontmatter([
        ("id", brief.id),
        ("kind", "chapter_brief"),
        ("target_ordinal", str(brief.target_ordinal)),
        ("status", brief.status.value),
    ])
    body = [f"\n# {brief.goal}\n"]
    if brief.pov_character_id:
        body.append(f"\nPOV: {brief.pov_character_id}\n")
    if brief.threads_to_touch:
        body.append(f"\nThreads: {', '.join(brief.threads_to_touch)}\n")
    if brief.beats_to_hit:
        body.append(f"\nBeats: {', '.join(brief.beats_to_hit)}\n")
    if brief.promises_to_progress:
        body.append(f"\nPromises: {', '.join(brief.promises_to_progress)}\n")
    if brief.value_shift:
        body.append(f"\nValue shift: {brief.value_shift}\n")
    if brief.planned_outcome:
        body.append(f"\nPlanned outcome: {brief.planned_outcome}\n")
    if brief.synopsis:
        body.append(f"\n## Synopsis\n\n{brief.synopsis}\n")
    return fm + "".join(body)


def render_threads_plan(threads: list[ThreadRecord], chapters: list[Chapter]) -> str:
    fm = _frontmatter([("kind", "threads_plan")])
    active = [t for t in threads if t.state.value not in TERMINAL_THREAD_STATES]
    lines = ["\n# Threads Plan\n"]
    for thread in active:
        window = (
            f"{thread.window_lo}-{thread.window_hi}"
            if thread.window_lo or thread.window_hi
            else "—"
        )
        payoff = thread.planned_payoff_note or "—"
        lines.append(
            f"- {thread.name}: state {thread.state.value}, window {window}, payoff: {payoff}"
        )
    return fm + "\n".join(lines) + "\n"


def render_ledger(promises: list[PromiseRecord], chapters: list[Chapter]) -> str:
    fm = _frontmatter([("kind", "ledger")])
    open_promises = [p for p in promises if p.state == PromiseState.open]
    overdue_ids = {p.id for p in overdue_promises(promises, chapters)}
    paid_count = sum(1 for p in promises if p.state == PromiseState.paid)
    released_count = sum(1 for p in promises if p.state == PromiseState.released)

    lines = ["\n# Ledger\n\n## Open\n"]
    for promise in open_promises:
        window = (
            f"{promise.window_lo}-{promise.window_hi}"
            if promise.window_lo or promise.window_hi
            else "—"
        )
        flag = " [overdue]" if promise.id in overdue_ids else ""
        lines.append(f"- {promise.name}: window {window}{flag}")

    lines.append(f"\n## Summary\n\npaid: {paid_count}\nreleased: {released_count}\n")
    return fm + "\n".join(lines)
