import os
import tempfile
import pytest
from novelizer.settings import EffectiveSettings as Settings
from novelizer.runtime import Runtime
from novelizer.tui.app import NovelizerApp
from novelizer.tui.approval_screen import ApprovalScreen
from novelizer.canon.events import EventType
from novelizer.canon.autonomy import AutonomyLevel, AutonomyState
from novelizer.store.models import Chapter
from novelizer.agents.schemas import (
    WorldEntriesDraft, KeeperOutput, EditorVerdict, ContinuityOutput, RetconAmendments, StructureAnalystOutput,
    SummarizerOutput,
)
from novelizer.agents.base import ChapterDraft
from tests.tui.conftest import stub_runners


class _R:
    def __init__(self, out): self._out = out
    async def ainvoke(self, inputs): return {"structured_response": self._out}


def _runners():
    return {k: _R(v) for k, v in {
        "world_architect": WorldEntriesDraft(), "author": ChapterDraft(title="X", prose="y"),
        "character_keeper": KeeperOutput(), "editor": EditorVerdict(), "continuity_checker": ContinuityOutput(),
        "retconner": RetconAmendments(),
        "structure_analyst": StructureAnalystOutput(),
        "summarizer": SummarizerOutput(gist="g", summary="s"),
    }.items()}


async def _gated_app(n_proposals: int = 1):
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    settings = Settings(db_path=path, projector_interval=0.1)
    rt = Runtime(settings, runners=stub_runners(**_runners()))
    await rt.start()
    for a in rt.scheduler.status():
        rt.scheduler.pause_agent(a["name"])
    await rt.events.append(EventType.AUTONOMY_CHANGED, "singleton",
                           AutonomyState(global_level=AutonomyLevel.gated_canon))
    await rt.projector.catch_up()
    for i in range(n_proposals):
        ch = Chapter(id=f"c{i + 1}", title=f"Pending {i + 1}", prose="It waits.")
        await rt.committer.commit("author", EventType.CHAPTER_CREATED, ch.id, ch)
    await rt.projector.catch_up()
    return NovelizerApp(rt), rt, path


@pytest.mark.asyncio
async def test_a_key_opens_modal_with_id_free_rows_and_context():
    app, rt, path = await _gated_app()
    try:
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("a")
            await pilot.pause()
            assert isinstance(app.screen, ApprovalScreen)
            from textual.widgets import OptionList, Static
            options = app.screen.query_one("#approval_list", OptionList)
            assert options.option_count == 1
            row = options.get_option_at_index(0).prompt.plain
            assert "Author" in row and "chapter.created" in row and "Pending 1" in row
            pending = await rt.read.list_proposals(status="open")
            assert pending[0].id[:8] not in row
            context = str(app.screen.query_one("#approval_context", Static).renderable)
            assert "Author proposes chapter.created" in context
            assert "It waits." in context
    finally:
        await rt.close(); os.unlink(path)


@pytest.mark.asyncio
async def test_a_key_does_nothing_when_no_open_proposals():
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    settings = Settings(db_path=path, projector_interval=0.1)
    rt = Runtime(settings, runners=stub_runners(**_runners()))
    await rt.start()
    for a in rt.scheduler.status():
        rt.scheduler.pause_agent(a["name"])
    app = NovelizerApp(rt)
    try:
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("a")
            await pilot.pause()
            assert not isinstance(app.screen, ApprovalScreen)
            assert len(app.screen_stack) == 1
    finally:
        await rt.close(); os.unlink(path)


@pytest.mark.asyncio
async def test_a_key_never_stacks_a_second_modal():
    app, rt, path = await _gated_app()
    try:
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("a")
            await pilot.pause()
            await pilot.press("a")   # falls through the modal to the app binding
            await pilot.pause()
            assert len(app.screen_stack) == 2  # default + one modal, never more
    finally:
        await rt.close(); os.unlink(path)


@pytest.mark.asyncio
async def test_enter_approves_through_dispatch_and_dismisses_when_queue_empties():
    app, rt, path = await _gated_app()
    try:
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("a")
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause(0.3)
            # the target event was committed for real
            chapters = await rt.read.list_chapters()
            assert len(chapters) == 1 and chapters[0].title == "Pending 1"
            # the result line landed in the feed like a typed command
            assert any(m.startswith("» Approved proposal") for m in app.messages)
            # queue empty -> modal dismissed itself
            assert not isinstance(app.screen, ApprovalScreen)
    finally:
        await rt.close(); os.unlink(path)


@pytest.mark.asyncio
async def test_x_rejects_and_second_proposal_stays_listed():
    app, rt, path = await _gated_app(n_proposals=2)
    try:
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("a")
            await pilot.pause()
            await pilot.press("x")
            await pilot.pause(0.3)
            assert any(m.startswith("» Rejected proposal") for m in app.messages)
            assert await rt.read.list_chapters() == []          # nothing committed
            assert isinstance(app.screen, ApprovalScreen)       # one proposal left
            from textual.widgets import OptionList
            assert app.screen.query_one("#approval_list", OptionList).option_count == 1
            assert len(await rt.read.list_proposals(status="open")) == 1
    finally:
        await rt.close(); os.unlink(path)


@pytest.mark.asyncio
async def test_escape_closes_without_deciding():
    app, rt, path = await _gated_app()
    try:
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("a")
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()
            assert not isinstance(app.screen, ApprovalScreen)
            assert len(await rt.read.list_proposals(status="open")) == 1
    finally:
        await rt.close(); os.unlink(path)
