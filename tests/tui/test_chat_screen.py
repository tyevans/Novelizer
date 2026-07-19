import os
import tempfile
import pytest
from textual.widgets import Input, RichLog, Tabs
from novelizer.settings import EffectiveSettings as Settings
from novelizer.runtime import Runtime
from novelizer.tui.app import NovelizerApp
from novelizer.tui.chat_screen import ChatScreen
from novelizer.canon.events import EventType, ChatUserMessaged, ChatAgentReplied
from novelizer.agents.base import ChapterDraft
from novelizer.agents.schemas import (
    WorldEntriesDraft, KeeperOutput, EditorVerdict, ContinuityOutput, RetconAmendments, StructureAnalystOutput,
)


class _R:
    def __init__(self, out): self._out = out
    async def ainvoke(self, inputs): return {"structured_response": self._out}


def _fake_agent_runners():
    """All seven autonomous agents faked — NovelizerApp's scheduler loop runs
    during run_test(), and an agent name missing from the runners dict would
    lazily build a REAL LLM runner (see Runtime._runner_for)."""
    return {k: _R(v) for k, v in {
        "world_architect": WorldEntriesDraft(), "author": ChapterDraft(title="X", prose="y"),
        "character_keeper": KeeperOutput(), "editor": EditorVerdict(), "continuity_checker": ContinuityOutput(),
        "retconner": RetconAmendments(), "structure_analyst": StructureAnalystOutput(),
    }.items()}


async def _runtime(path):
    settings = Settings(db_path=path, projector_interval=0.05)
    rt = Runtime(settings, runners=_fake_agent_runners())
    await rt.start()
    return rt


@pytest.fixture
def db_path():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    os.unlink(path)


@pytest.mark.asyncio
async def test_screen_renders_transcript_and_tabs(db_path):
    rt = await _runtime(db_path)
    try:
        await rt.events.append(
            EventType.CHAT_USER_MESSAGED, "author",
            ChatUserMessaged(message_id="m1", agent_name="author", text="hello author"),
        )
        await rt.events.append(
            EventType.CHAT_AGENT_REPLIED, "author",
            ChatAgentReplied(message_id="m2", agent_name="author", text="hello Director"),
        )
        await rt.projector.catch_up()
        app = NovelizerApp(rt)
        async with app.run_test() as pilot:
            await app.push_screen(ChatScreen(rt, "author"))
            await pilot.pause(0.8)
            assert isinstance(app.screen, ChatScreen)
            log = app.screen.query_one("#chat_log", RichLog)
            text = "\n".join(str(line) for line in log.lines)
            assert "hello Director" in text
            tabs = app.screen.query_one("#chat_tabs", Tabs)
            assert tabs.tab_count >= 1
    finally:
        await rt.close()


@pytest.mark.asyncio
async def test_input_submit_calls_app_send_and_escape_pops(db_path):
    rt = await _runtime(db_path)
    try:
        app = NovelizerApp(rt)
        sent = []

        async def fake_send(agent, text):
            sent.append((agent, text))

        app.send_chat_message = fake_send
        async with app.run_test() as pilot:
            await app.push_screen(ChatScreen(rt, "author"))
            await pilot.pause(0.2)
            inp = app.screen.query_one("#chat_input", Input)
            inp.value = "what about the shaft?"
            app.set_focus(inp)
            await pilot.press("enter")
            await pilot.pause(0.2)
            assert sent == [("author", "what about the shaft?")]
            await pilot.press("escape")
            await pilot.pause(0.2)
            assert not isinstance(app.screen, ChatScreen)
    finally:
        await rt.close()
