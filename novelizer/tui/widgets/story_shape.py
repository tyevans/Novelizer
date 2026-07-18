from __future__ import annotations
from textual.widgets import Static
from novelizer.brain.sag_spike import detect_sag_spike
from novelizer.store.models import StructureScore


def story_shape_line(score: StructureScore, flag: str | None) -> str:
    marker = f"  [{flag.upper()}]" if flag else ""
    return f"· {score.chapter_id}  tension={score.tension:.2f}  {score.pacing_label}{marker}"


class StoryShape(Static):
    async def refresh_from(self, read) -> None:
        scores = await read.list_structure_scores()
        flags = detect_sag_spike(scores)
        lines = [story_shape_line(s, flags.get(s.chapter_id)) for s in scores]
        self.update("\n".join(lines) or "no chapters scored yet")
