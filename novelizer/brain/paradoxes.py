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


def _strongly_connected_components(adjacency: dict[str, list[str]]) -> dict[str, int]:
    """Tarjan's SCC algorithm, plain dicts/lists, no graph library (M4
    Locked decision #4). Returns a map from node id to its SCC index.
    Recursive: story-scale chapter graphs are small, so recursion depth is
    not a practical concern here.
    """
    index_counter = [0]
    indices: dict[str, int] = {}
    lowlink: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    scc_of: dict[str, int] = {}
    scc_counter = [0]

    def strongconnect(node: str) -> None:
        indices[node] = index_counter[0]
        lowlink[node] = index_counter[0]
        index_counter[0] += 1
        stack.append(node)
        on_stack.add(node)

        for neighbor in adjacency.get(node, []):
            if neighbor not in indices:
                strongconnect(neighbor)
                lowlink[node] = min(lowlink[node], lowlink[neighbor])
            elif neighbor in on_stack:
                lowlink[node] = min(lowlink[node], indices[neighbor])

        if lowlink[node] == indices[node]:
            scc_id = scc_counter[0]
            scc_counter[0] += 1
            while True:
                w = stack.pop()
                on_stack.discard(w)
                scc_of[w] = scc_id
                if w == node:
                    break

    all_nodes = set(adjacency)
    for neighbors in adjacency.values():
        all_nodes.update(neighbors)
    for node in all_nodes:
        if node not in indices:
            strongconnect(node)

    return scc_of


def _cycle_edges(edges: list[CausalEdgeRecord]) -> set[tuple[str, str]]:
    """Every (cause, effect) pair that participates in some cycle in the
    adjacency list built from `edges`: an edge (u, v) lies on a cycle iff u
    and v belong to the same strongly connected component of size > 1, or
    u == v (a self-edge -- shouldn't occur since commit-time drops them,
    but is handled defensively as a cycle edge since the projection
    records whatever is in the log). Computed via Tarjan's SCC algorithm
    rather than a path-DFS, because a path-DFS only catches back-edges
    along the currently active path and misses cross-edges into an
    already-fully-visited node that still closes a *different* cycle
    sharing that node. Duplicate declared edges (no dedup at the
    projection level) are naturally handled: the adjacency list carries
    them as repeated entries, and each occurrence is checked independently.
    """
    adjacency: dict[str, list[str]] = {}
    for e in edges:
        adjacency.setdefault(e.cause_chapter_id, []).append(e.effect_chapter_id)

    scc_of = _strongly_connected_components(adjacency)
    scc_size: dict[int, int] = {}
    for scc_id in scc_of.values():
        scc_size[scc_id] = scc_size.get(scc_id, 0) + 1

    cyclic: set[tuple[str, str]] = set()
    for e in edges:
        u, v = e.cause_chapter_id, e.effect_chapter_id
        if u == v:
            cyclic.add((u, v))
        elif scc_of.get(u) is not None and scc_of.get(u) == scc_of.get(v) and scc_size[scc_of[u]] > 1:
            cyclic.add((u, v))
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
