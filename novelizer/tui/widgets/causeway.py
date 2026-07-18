from __future__ import annotations
from textual.widgets import Static
from novelizer.brain.paradoxes import find_paradoxes
from novelizer.store.models import CausalEdgeRecord


def causeway_line(edge: CausalEdgeRecord, is_paradox: bool) -> str:
    marker = "  [PARADOX]" if is_paradox else ""
    return f"· chapter {edge.cause_chapter_id} → chapter {edge.effect_chapter_id}: {edge.note}{marker}"


class Causeway(Static):
    async def refresh_from(self, read) -> None:
        edges = await read.list_causal_edges()
        chapters = await read.list_chapters()
        chapter_order = [c.id for c in chapters]
        paradox_pairs = {
            (p.cause_chapter_id, p.effect_chapter_id) for p in find_paradoxes(edges, chapter_order)
        }
        ordered = sorted(edges, key=lambda e: (e.cause_chapter_id, e.effect_chapter_id))
        lines = [
            causeway_line(e, (e.cause_chapter_id, e.effect_chapter_id) in paradox_pairs) for e in ordered
        ]
        self.update("\n".join(lines) or "no causal edges yet")
