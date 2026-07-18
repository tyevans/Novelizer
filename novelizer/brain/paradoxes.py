from __future__ import annotations
from pydantic import BaseModel
from novelizer.store.models import CausalEdgeRecord

PARADOX_SOURCE_TAG = "[source: paradox_detector]"


class ParadoxCandidate(BaseModel):
    """A causal edge flagged as internally inconsistent. Never persisted --
    computed fresh from ReadStore data every time find_paradoxes runs
    (novelizer/agents/continuity_checker.py, and later M4.3's Causeway
    render helper), same precedent as novelizer/brain/sag_spike.py.
    `reason` is "ordering" (effect chapter is at-or-before cause chapter)
    or "cycle" (this edge closes a cycle in the adjacency list).
    """

    cause_chapter_id: str
    effect_chapter_id: str
    note: str = ""
    reason: str


def _cycle_edges(edges: list[CausalEdgeRecord]) -> set[tuple[str, str]]:
    """Every (cause, effect) pair that lies on some cycle in the adjacency
    list built from `edges`. Plain dict-of-lists DFS, no graph library
    (M4 Locked decision #4). Duplicate declared edges (no dedup at the
    projection level) are naturally handled: the adjacency list carries
    them as repeated entries, and each occurrence is checked independently.
    """
    adjacency: dict[str, list[str]] = {}
    for e in edges:
        adjacency.setdefault(e.cause_chapter_id, []).append(e.effect_chapter_id)

    cyclic: set[tuple[str, str]] = set()
    visiting: set[str] = set()
    visited: set[str] = set()
    path: list[str] = []

    def dfs(node: str) -> None:
        visiting.add(node)
        path.append(node)
        for neighbor in adjacency.get(node, []):
            if neighbor in visiting:
                idx = path.index(neighbor)
                cycle_nodes = path[idx:] + [neighbor]
                for a, b in zip(cycle_nodes, cycle_nodes[1:]):
                    cyclic.add((a, b))
            elif neighbor not in visited:
                dfs(neighbor)
        path.pop()
        visiting.discard(node)
        visited.add(node)

    for node in list(adjacency):
        if node not in visited:
            dfs(node)
    return cyclic


def find_paradoxes(edges: list[CausalEdgeRecord], chapter_order: list[str]) -> list[ParadoxCandidate]:
    """An edge is a paradox candidate if its effect chapter's index in
    `chapter_order` is at or before its cause chapter's index ("ordering"),
    or if it participates in a cycle in the declared-edge adjacency list
    ("cycle") -- every edge on a cycle is its own candidate, so a 2-cycle
    yields two candidates (M4 spec's done-when). An edge citing a chapter
    id absent from `chapter_order` cannot be ordering-checked and is
    skipped for that check (chapter ids are validated to exist at commit
    time by BaseAgent._commit_causal_intents, so this should not occur in
    practice) but is still eligible for the cycle check.
    """
    index = {cid: i for i, cid in enumerate(chapter_order)}
    cyclic_pairs = _cycle_edges(edges)
    candidates: list[ParadoxCandidate] = []
    for e in edges:
        cause_idx = index.get(e.cause_chapter_id)
        effect_idx = index.get(e.effect_chapter_id)
        if cause_idx is not None and effect_idx is not None and effect_idx <= cause_idx:
            candidates.append(ParadoxCandidate(
                cause_chapter_id=e.cause_chapter_id, effect_chapter_id=e.effect_chapter_id,
                note=e.note, reason="ordering",
            ))
        elif (e.cause_chapter_id, e.effect_chapter_id) in cyclic_pairs:
            candidates.append(ParadoxCandidate(
                cause_chapter_id=e.cause_chapter_id, effect_chapter_id=e.effect_chapter_id,
                note=e.note, reason="cycle",
            ))
    return candidates


def paradox_description(p: ParadoxCandidate) -> str:
    """The single place a paradox's retcon-request description is
    formatted. Deterministic given the same edge -- the Continuity
    Checker's dedup check (novelizer/agents/continuity_checker.py) relies
    on this to recognize "the same paradox" across polling cycles.
    """
    return (
        f"{PARADOX_SOURCE_TAG} causal edge {p.cause_chapter_id} -> {p.effect_chapter_id} "
        f"is a paradox candidate ({p.reason})."
    )
