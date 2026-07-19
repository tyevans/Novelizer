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
            # The statusbar is now the glyph strip + dial: the cast's glyphs
            # are always present once _statusbar_loop has run, and the dial
            # renders 'AUTONOMY ▮...' — no agent names appear by design.
            while time.monotonic() < deadline:
                await pilot.pause(0.2)
                statusbar_text = str(app.query_one("#statusbar", Static).renderable)
                tree = app.query_one("#browser", Tree)
                all_labels = [str(n.label) for n in tree.root.children] + [str(c.label) for n in tree.root.children for c in n.children]
                if "AUTONOMY" in statusbar_text and (
                    any("Chapter One" in l for l in all_labels) or any("Chapters (1" in l for l in all_labels)
                ):
                    break
            # statusbar shows the cast glyph strip and the dial; browser shows the authored chapter
            assert "✎" in statusbar_text and "∿" in statusbar_text
            assert "AUTONOMY ▮" in statusbar_text
            assert any("Chapter One" in l for l in all_labels) or any("Chapters (1" in l for l in all_labels)
    finally:
        await rt.close(); os.unlink(path)


@pytest.mark.asyncio
async def test_proposals_banner_appears_and_approve_via_command_clears_it():
    from textual.widgets import Static
    from novelizer.canon.events import EventType
    from novelizer.canon.autonomy import AutonomyLevel, AutonomyState
    from novelizer.store.models import Chapter

    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    settings = Settings(db_path=path, projector_interval=0.1)
    rt = Runtime(settings, runners=_runners())
    await rt.start()
    # Pause all background agents to ensure deterministic test (only the intended proposal exists)
    for name in ["world_architect", "character_keeper", "author", "editor",
                 "continuity_checker", "retconner", "structure_analyst"]:
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
            await pilot.pause(0.7)  # let _proposals_loop cycle
            banner = app.query_one("#proposals_banner", Static)
            assert banner.display, "banner must be visible while a proposal is open"
            banner_text = str(banner.renderable)
            assert banner_text == "▼ 1 proposal awaiting approval — press a"
            pending = await rt.read.list_proposals(status="open")
            assert len(pending) == 1
            assert pending[0].id[:8] not in banner_text   # id-free dashboard
            # the resident pane is gone
            assert not app.query("#proposals")
            # approving through the command seam still works and empties the queue
            await app._run_command(f"approve {pending[0].id}")
            await rt.projector.catch_up()
            chapters = await rt.read.list_chapters()
            assert len(chapters) == 1 and chapters[0].title == "Pending One"
            await pilot.pause(0.7)
            assert not app.query_one("#proposals_banner", Static).display
    finally:
        await rt.close(); os.unlink(path)


@pytest.mark.asyncio
async def test_proposals_banner_stays_hidden_in_engine_mode():
    from textual.widgets import Static
    from novelizer.canon.events import EventType
    from novelizer.canon.autonomy import AutonomyLevel, AutonomyState
    from novelizer.store.models import Chapter

    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    settings = Settings(db_path=path, projector_interval=0.1)
    rt = Runtime(settings, runners=_runners())
    await rt.start()
    for name in ["world_architect", "character_keeper", "author", "editor",
                 "continuity_checker", "retconner", "structure_analyst"]:
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
            await pilot.pause(0.7)  # let _proposals_loop cycle
            banner = app.query_one("#proposals_banner", Static)
            assert banner.display, "banner must be visible while a proposal is open"
            await pilot.press("e")
            await pilot.pause(0.7)  # loop keeps running while the Engine Room is up
            assert app.query_one("#body").has_class("engine")
            assert not banner.display, "banner must not leak into Engine Room mode"
            await pilot.press("e")
            await pilot.pause(0.7)
            assert banner.display, "banner must return when leaving Engine Room mode"
    finally:
        await rt.close(); os.unlink(path)


@pytest.mark.asyncio
async def test_proposals_banner_hidden_on_a_quiet_story():
    from textual.widgets import Static

    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    settings = Settings(db_path=path, projector_interval=0.1)
    rt = Runtime(settings, runners=_runners())
    await rt.start()
    for name in ["world_architect", "character_keeper", "author", "editor",
                 "continuity_checker", "retconner", "structure_analyst"]:
        rt.scheduler.pause_agent(name)
    app = NovelizerApp(rt)
    try:
        async with app.run_test() as pilot:
            await pilot.pause(0.7)
            assert not app.query_one("#proposals_banner", Static).display
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
                "#brain": "STORY BRAIN",
                "#browser": "STORY",
                "#detail_scroll": "DETAIL",
            }
            for selector, title in expected.items():
                assert str(app.query_one(selector).border_title) == title, selector
    finally:
        await rt.close(); os.unlink(path)


@pytest.mark.asyncio
async def test_detail_border_title_follows_selection_and_resets():
    from types import SimpleNamespace
    from textual.containers import VerticalScroll
    from novelizer.canon.events import EventType
    from novelizer.store.models import Chapter

    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    settings = Settings(db_path=path, projector_interval=0.1)
    rt = Runtime(settings, runners=_runners())
    await rt.start()
    for name in ["world_architect", "character_keeper", "author", "editor",
                 "continuity_checker", "retconner", "structure_analyst"]:
        rt.scheduler.pause_agent(name)
    try:
        await rt.events.append(EventType.CHAPTER_CREATED, "c1",
                               Chapter(id="c1", title="The Name in the Wind", prose="wind words"))
        await rt.projector.catch_up()
        app = NovelizerApp(rt)
        async with app.run_test() as pilot:
            await pilot.pause()
            scroll = app.query_one("#detail_scroll", VerticalScroll)
            assert str(scroll.border_title) == "DETAIL"
            event = SimpleNamespace(node=SimpleNamespace(data={"section": "chapters", "id": "c1"}))
            await app.on_tree_node_selected(event)
            await pilot.pause()
            assert str(scroll.border_title) == "THE NAME IN THE WIND"
            # a miss resets the pane to its quiet label
            event = SimpleNamespace(node=SimpleNamespace(data={"section": "chapters", "id": "ghost"}))
            await app.on_tree_node_selected(event)
            await pilot.pause()
            assert str(scroll.border_title) == "DETAIL"
    finally:
        await rt.close(); os.unlink(path)
