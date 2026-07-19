import os
import tempfile
import pytest
from novelizer.settings import EffectiveSettings as Settings
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
            assert app.query_one("#statusbar", Static) is not None
            # the roster pane is gone: agent state lives in the status bar now
            assert not app.query("#roster")
            import time
            deadline = time.monotonic() + 5.0
            statusbar_text = ""
            all_labels = []
            # Under the M5.3 concurrency pool, agents complete in milliseconds
            # and the bar names the in-flight set or the most recent completer
            # -- WHICH agent is named at any sampled instant is a race, so the
            # deterministic form of "the status bar shows the active agent" is
            # that SOME roster agent is named, not "author" specifically.
            _roster_names = (
                "author", "world_architect", "character_keeper", "editor",
                "continuity_checker", "retconner", "structure_analyst",
            )
            while time.monotonic() < deadline:
                await pilot.pause(0.2)
                statusbar_text = str(app.query_one("#statusbar", Static).renderable)
                tree = app.query_one("#browser", Tree)
                all_labels = [str(n.label) for n in tree.root.children] + [str(c.label) for n in tree.root.children for c in n.children]
                if any(n in statusbar_text for n in _roster_names) and (
                    any("Chapter One" in l for l in all_labels) or any("Chapters (1" in l for l in all_labels)
                ):
                    break
            # status bar shows the active agent; browser shows the authored chapter
            assert any(n in statusbar_text for n in _roster_names)
            assert "AUTONOMY" in statusbar_text
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


@pytest.mark.asyncio
async def test_story_brain_threads_and_shape_tabs_populate():
    from novelizer.canon.events import EventType, ThreadPlanted, AnnotationStructureScored
    from novelizer.store.models import Chapter

    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    settings = Settings(db_path=path, projector_interval=0.1)
    rt = Runtime(settings, runners=_runners())
    await rt.start()
    for name in ["world_architect", "character_keeper", "author", "editor", "continuity_checker", "retconner", "structure_analyst"]:
        rt.scheduler.pause_agent(name)
    try:
        await rt.events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="The Salt Road", prose="p"))
        await rt.events.append(EventType.THREAD_PLANTED, "the-locket", ThreadPlanted(id="the-locket", name="The Locket"))
        await rt.events.append(EventType.ANNOTATION_STRUCTURE_SCORED, "c1",
                               AnnotationStructureScored(chapter_id="c1", tension=0.6, pacing_label="rising"))
        await rt.projector.catch_up()
        app = NovelizerApp(rt)
        async with app.run_test() as pilot:
            from textual.widgets import Sparkline, Static
            await pilot.pause(0.5)
            threads_text = str(app.query_one("#threads_body", Static).renderable)
            shape_text = str(app.query_one("#shape_body", Static).renderable)
            spark = app.query_one("#shape_spark", Sparkline)
            assert "The Locket" in threads_text
            assert "the-locket" not in threads_text          # no ids on the dashboard
            assert list(spark.data) == [0.6] and spark.display
            assert "pacing: rising" in shape_text
            assert "c1" not in shape_text                    # no ids on the dashboard
    finally:
        await rt.close(); os.unlink(path)


@pytest.mark.asyncio
async def test_story_brain_secrets_matrix_and_causeway_tabs_populate():
    from novelizer.canon.events import EventType, SecretCreated, SecretLearned, CausalEdgeDeclared
    from novelizer.store.models import Chapter, Character

    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    settings = Settings(db_path=path, projector_interval=0.1)
    rt = Runtime(settings, runners=_runners())
    await rt.start()
    for name in ["world_architect", "character_keeper", "author", "editor", "continuity_checker", "retconner", "structure_analyst"]:
        rt.scheduler.pause_agent(name)
    try:
        await rt.events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
        await rt.events.append(EventType.CHARACTER_CREATED, "mara", Character(id="mara", name="Mara"))
        await rt.events.append(EventType.SECRET_CREATED, "the-heir-lives", SecretCreated(id="the-heir-lives", title="The Heir Lives"))
        await rt.events.append(EventType.SECRET_LEARNED, "the-heir-lives", SecretLearned(id="the-heir-lives", character_id="mara"))
        await rt.events.append(EventType.CHAPTER_CREATED, "c2", Chapter(id="c2", title="Two", prose="p"))
        await rt.events.append(EventType.CAUSAL_EDGE_DECLARED, "c1",
                               CausalEdgeDeclared(cause_chapter_id="c2", effect_chapter_id="c1"))
        await rt.projector.catch_up()
        app = NovelizerApp(rt)
        async with app.run_test() as pilot:
            from textual.widgets import Static
            await pilot.pause(0.5)
            secrets_text = str(app.query_one("#secrets_body", Static).renderable)
            causeway_text = str(app.query_one("#causeway_body", Static).renderable)
            strip_text = str(app.query_one("#brain_strip", Static).renderable)
            assert "The Heir Lives" in secrets_text
            assert "M" in secrets_text.splitlines()[0]       # Mara's initial in the header
            assert "●" in secrets_text and "1 knows" in secrets_text
            assert "the-heir-lives" not in secrets_text      # no ids on the dashboard
            assert 'ch 2 "Two" ──▶ ch 1 "One"' in causeway_text
            assert "⚠ PARADOX" in causeway_text
            assert "Cause ⚠1" in strip_text
    finally:
        await rt.close(); os.unlink(path)


@pytest.mark.asyncio
async def test_every_pane_has_its_border_title():
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    settings = Settings(db_path=path, projector_interval=0.1)
    rt = Runtime(settings, runners=_runners())
    await rt.start()
    for name in ["world_architect", "character_keeper", "author", "editor",
                 "continuity_checker", "retconner", "structure_analyst"]:
        rt.scheduler.pause_agent(name)
    app = NovelizerApp(rt)
    try:
        async with app.run_test():
            expected = {
                "#feed": "THE ROOM",
                "#proposals": "PROPOSALS",
                "#brain": "STORY BRAIN",
                "#browser": "STORY",
                "#detail_scroll": "DETAIL",
            }
            for selector, title in expected.items():
                assert str(app.query_one(selector).border_title) == title, selector
    finally:
        await rt.close(); os.unlink(path)
