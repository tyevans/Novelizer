import os
import tempfile
import pytest
from novelizer.config import Settings
from novelizer.runtime import Runtime
from novelizer.tui.app import NovelizerApp
from novelizer.agents.schemas import (
    WorldEntriesDraft, WorldEntryDraft, KeeperOutput, EditorVerdict, ContinuityOutput, RetconAmendments,
    StructureAnalystOutput,
)
from novelizer.agents.base import ChapterDraft


class _R:
    def __init__(self, out): self._out = out
    async def ainvoke(self, inputs): return {"structured_response": self._out}


def _runners():
    return {
        "world_architect": _R(WorldEntriesDraft(entries=[WorldEntryDraft(title="Brinemarsh", body="salt")])),
        "author": _R(ChapterDraft(title="Chapter One", prose="It began.")),
        "character_keeper": _R(KeeperOutput()),
        "editor": _R(EditorVerdict(verdict="approve", notes="ok")),
        "continuity_checker": _R(ContinuityOutput()),
        "retconner": _R(RetconAmendments()),
        "structure_analyst": _R(StructureAnalystOutput()),
    }


@pytest.mark.asyncio
async def test_mission_control_panes_present_and_populate():
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    settings = Settings(db_path=path, author_interval=1, projector_interval=0.1, default_agent_interval=1, continuity_interval=1)
    rt = Runtime(settings, runners=_runners())
    await rt.start()
    app = NovelizerApp(rt)
    try:
        async with app.run_test() as pilot:
            from textual.widgets import RichLog, Tree, Static
            assert app.query_one("#feed", RichLog) is not None
            assert app.query_one("#browser", Tree) is not None
            assert app.query_one("#roster", Static) is not None
            assert app.query_one("#statusbar", Static) is not None
            import time
            deadline = time.monotonic() + 5.0
            roster_text = ""
            all_labels = []
            while time.monotonic() < deadline:
                await pilot.pause(0.2)
                roster_text = str(app.query_one("#roster", Static).renderable)
                tree = app.query_one("#browser", Tree)
                all_labels = [str(n.label) for n in tree.root.children] + [str(c.label) for n in tree.root.children for c in n.children]
                if "author" in roster_text and (
                    any("Chapter One" in l for l in all_labels) or any("Chapters (1" in l for l in all_labels)
                ):
                    break
            # roster shows agent names; browser shows the authored chapter
            assert "author" in roster_text
            assert any("Chapter One" in l for l in all_labels) or any("Chapters (1" in l for l in all_labels)
    finally:
        await rt.close(); os.unlink(path)


@pytest.mark.asyncio
async def test_approval_queue_pane_shows_pending_proposal_and_approve_via_command():
    from textual.widgets import Static
    from novelizer.canon.events import EventType
    from novelizer.canon.autonomy import AutonomyLevel, AutonomyState
    from novelizer.store.models import Chapter

    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    settings = Settings(db_path=path, projector_interval=0.1)
    rt = Runtime(settings, runners=_runners())
    await rt.start()
    # Pause all background agents to ensure deterministic test (only the intended proposal exists)
    for name in ["world_architect", "character_keeper", "author", "editor", "continuity_checker", "retconner"]:
        rt.scheduler.pause_agent(name)
    try:
        await rt.events.append(EventType.AUTONOMY_CHANGED, "singleton",
                                AutonomyState(global_level=AutonomyLevel.gated_canon))
        await rt.projector.catch_up()
        ch = Chapter(id="c1", title="Pending One", prose="p")
        await rt.committer.commit("author", EventType.CHAPTER_CREATED, ch.id, ch)
        await rt.projector.catch_up()
        app = NovelizerApp(rt)
        async with app.run_test() as pilot:
            await pilot.pause()
            proposals_widget = app.query_one("#proposals", Static)
            pending = await rt.read.list_proposals(status="open")
            assert len(pending) == 1
            proposal_id = pending[0].id
            proposals_text = str(proposals_widget.renderable)
            assert proposal_id[:8] in proposals_text or "chapter.created" in proposals_text
            await app._run_command(f"approve {proposal_id}")
            await rt.projector.catch_up()
            chapters = await rt.read.list_chapters()
            assert len(chapters) == 1 and chapters[0].title == "Pending One"
    finally:
        await rt.close(); os.unlink(path)
