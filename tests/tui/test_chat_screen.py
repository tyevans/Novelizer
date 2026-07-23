import os
import tempfile
import pytest
from textual.widgets import Input, RichLog, Tab, Tabs
from novelizer.settings import EffectiveSettings as Settings
from novelizer.runtime import Runtime
from novelizer.tui.app import NovelizerApp
from novelizer.tui.chat_screen import ChatScreen
from novelizer.canon.events import EventType, ChatUserMessaged, ChatAgentReplied
from novelizer.agents.base import ChapterDraft
from novelizer.agents.schemas import (
    WorldEntriesDraft, KeeperOutput, EditorVerdict, ContinuityOutput, RetconAmendments, StructureAnalystOutput,
    SummarizerOutput,
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
        "summarizer": SummarizerOutput(gist="g", summary="s"),
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
async def test_unread_dot_appears_on_unfocused_conversation(db_path):
    rt = await _runtime(db_path)
    try:
        await rt.events.append(
            EventType.CHAT_USER_MESSAGED, "author",
            ChatUserMessaged(message_id="a1", agent_name="author", text="hi author"),
        )
        await rt.events.append(
            EventType.CHAT_AGENT_REPLIED, "author",
            ChatAgentReplied(message_id="a2", agent_name="author", text="reply to author"),
        )
        await rt.events.append(
            EventType.CHAT_USER_MESSAGED, "editor",
            ChatUserMessaged(message_id="e1", agent_name="editor", text="hi editor"),
        )
        await rt.events.append(
            EventType.CHAT_AGENT_REPLIED, "editor",
            ChatAgentReplied(message_id="e2", agent_name="editor", text="reply to editor"),
        )
        await rt.projector.catch_up()
        app = NovelizerApp(rt)
        async with app.run_test() as pilot:
            await app.push_screen(ChatScreen(rt, "author"))
            await pilot.pause(0.8)

            editor_label = str(app.screen.query_one("#chat-editor", Tab).label)
            author_label = str(app.screen.query_one("#chat-author", Tab).label)
            assert editor_label.endswith("●")
            assert not author_label.endswith("●")

            await rt.events.append(
                EventType.CHAT_AGENT_REPLIED, "editor",
                ChatAgentReplied(message_id="e3", agent_name="editor", text="another reply"),
            )
            await rt.projector.catch_up()
            await pilot.pause(0.8)
            editor_label = str(app.screen.query_one("#chat-editor", Tab).label)
            assert editor_label.endswith("●")

            await app.screen.set_current("editor")
            await pilot.pause(0.8)
            editor_label = str(app.screen.query_one("#chat-editor", Tab).label)
            assert not editor_label.endswith("●")
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


from novelizer.telemetry.events import TelemetryEventType, AgentRunStarted, TokenDelta
from tui_kit.widgets.live_stream_panel import LiveStreamPanel


@pytest.mark.asyncio
async def test_panel_shows_stream_for_the_active_agent_only(db_path):
    rt = await _runtime(db_path)
    try:
        await rt.events.append(
            EventType.CHAT_USER_MESSAGED, "author",
            ChatUserMessaged(message_id="m1", agent_name="author", text="hi"),
        )
        await rt.projector.catch_up()
        app = NovelizerApp(rt)
        async with app.run_test() as pilot:
            screen = ChatScreen(rt, "author")
            await app.push_screen(screen)
            await pilot.pause()

            await rt.telemetry.emit(TelemetryEventType.AGENT_RUN_STARTED, "r1",
                                    AgentRunStarted(run_id="r1", agent_name="chat:author"))
            rt.telemetry.publish_token(TokenDelta(run_id="r1", agent_name="chat:author", text="drafting…"))
            await pilot.pause(0.2)

            panel = screen.query_one(LiveStreamPanel)
            body = panel.query_one(LiveStreamPanel._STREAM_ID)
            assert "drafting…" in str(body.renderable)
    finally:
        await rt.close()


@pytest.mark.asyncio
async def test_switching_conversation_resets_the_panel(db_path):
    rt = await _runtime(db_path)
    try:
        app = NovelizerApp(rt)
        async with app.run_test() as pilot:
            screen = ChatScreen(rt, "author")
            await app.push_screen(screen)
            await pilot.pause()

            await rt.telemetry.emit(TelemetryEventType.AGENT_RUN_STARTED, "r1",
                                    AgentRunStarted(run_id="r1", agent_name="chat:author"))
            rt.telemetry.publish_token(TokenDelta(run_id="r1", agent_name="chat:author", text="drafting…"))
            await pilot.pause(0.2)

            await screen.set_current("editor")
            await pilot.pause(0.1)

            panel = screen.query_one(LiveStreamPanel)
            body = panel.query_one(LiveStreamPanel._STREAM_ID)
            assert "drafting…" not in str(body.renderable)
    finally:
        await rt.close()
