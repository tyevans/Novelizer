from __future__ import annotations
from novelizer.brain.beat_drift import beat_drifts
from novelizer.brain.completion import completion_status
from novelizer.brain.leaks import find_leaks, leak_description
from novelizer.brain.ledger import due_promises, overdue_promises
from novelizer.brain.paradoxes import find_paradoxes, paradox_description
from novelizer.brain.staleness import stale_threads


def _substitute_slugs(blocker: str, beat_names_by_slug: dict[str, str]) -> str:
    prefix, _, slugs = blocker.rpartition(": ")
    if not prefix or not slugs:
        return blocker
    names = ", ".join(beat_names_by_slug.get(s.strip(), s.strip()) for s in slugs.split(","))
    return f"{prefix}: {names}"


async def check_stale_threads(read) -> str:
    threads = await read.list_threads()
    chapters = await read.list_chapters()
    stale = stale_threads(threads, chapters)
    if not stale:
        return "No stale threads."
    lines = "\n".join(f"- {t.name} (id:{t.id})" for t in stale)
    return f"Stale threads:\n{lines}"


async def check_leaks(read) -> str:
    references = await read.list_secret_references()
    matrix = await read.knowledge_matrix()
    leaks = find_leaks(references, matrix)
    if not leaks:
        return "No leaks found."
    lines = "\n".join(f"- {leak_description(leak)}" for leak in leaks)
    return f"Leaks:\n{lines}"


async def check_paradoxes(read) -> str:
    edges = await read.list_causal_edges()
    chapter_order = [c.id for c in await read.list_chapters()]
    paradoxes = find_paradoxes(edges, chapter_order)
    if not paradoxes:
        return "No paradoxes found."
    lines = "\n".join(f"- {paradox_description(p)}" for p in paradoxes)
    return f"Paradoxes:\n{lines}"


async def check_promise_ledger(read) -> str:
    promises = await read.list_promises()
    chapters = await read.list_chapters()
    overdue = overdue_promises(promises, chapters)
    due = due_promises(promises, chapters)
    if not overdue and not due:
        return "No overdue or due promises."
    lines = [f"- {p.name} (id:{p.id}) — OVERDUE — window closed ch {p.window_hi}" for p in overdue]
    lines += [f"- {p.name} (id:{p.id}) — due ch {p.window_lo}-{p.window_hi}" for p in due]
    return "Promise ledger:\n" + "\n".join(lines)


async def check_beat_drift(read) -> str:
    blueprint = await read.get_active_blueprint()
    if blueprint is None:
        return "No beat drift (no adopted blueprint)."
    beats = await read.list_beats()
    chapters = await read.list_chapters()
    drifts = beat_drifts(blueprint, beats, chapters)
    if not drifts:
        return "No beat drift."
    lines = "\n".join(f"- {d.name} ({d.kind}): {d.detail}" for d in drifts)
    return f"Beat drift:\n{lines}"


async def check_completion(read) -> str:
    blueprint = await read.get_active_blueprint()
    if blueprint is None:
        return "No adopted blueprint yet."
    beats = await read.list_beats()
    promises = await read.list_promises()
    arcs = await read.list_arcs()
    chapters = await read.list_chapters()
    status = completion_status(blueprint, beats, promises, arcs, chapters)
    if status.complete:
        return "Complete: every beat fulfilled, every promise settled, every arc resolved."
    beat_names_by_slug = {b.slug: b.name for b in beats}
    blockers = [
        _substitute_slugs(blocker, beat_names_by_slug) for blocker in status.blockers
    ]
    lines = "\n".join(f"- {b}" for b in blockers)
    return (
        f"Not complete ({status.beats_fulfilled}/{status.beats_total} beats, "
        f"{status.promises_open} promises open, {status.arcs_unresolved} arcs unresolved):\n{lines}"
    )
