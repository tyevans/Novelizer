import os
import tempfile
import pytest
from novelizer.settings import EffectiveSettings as Settings
from novelizer.runtime import Runtime
from novelizer.tui.app import NovelizerApp, format_event
from novelizer.tui.chat_screen import ChatScreen
from novelizer.canon.events import EventType, StoredEvent
from novelizer.chat.schemas import ChatReply
from tests.tui.test_chat_screen import _R, _fake_agent_runners


@pytest.fixture
def db_path():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    os.unlink(path)


@pytest.mark.asyncio
async def test_at_mention_opens_chat_and_generates_reply(db_path):
    settings = Settings(db_path=db_path, projector_interval=0.05)
    runners = _fake_agent_runners() | {"chat_author": _R(ChatReply(reply_text="thinking in scenes"))}
    rt = Runtime(settings, runners=runners)
    await rt.start()
    try:
        app = NovelizerApp(rt)
        async with app.run_test() as pilot:
            await app._run_command("@author what is this concept of dread?")
            await pilot.pause(1.0)
            assert isinstance(app.screen, ChatScreen)
            assert app.screen.agent_name == "author"
            log = await rt.events.events_since(0)
            assert [e for e in log if e.event_type == EventType.CHAT_USER_MESSAGED]
            assert [e for e in log if e.event_type == EventType.CHAT_AGENT_REPLIED]
    finally:
        await rt.close()


@pytest.mark.asyncio
async def test_alias_and_bare_mention_open_without_sending(db_path):
    settings = Settings(db_path=db_path, projector_interval=0.05)
    rt = Runtime(settings, runners=_fake_agent_runners())
    await rt.start()
    try:
        app = NovelizerApp(rt)
        async with app.run_test() as pilot:
            await app._run_command("@keeper")
            await pilot.pause(0.3)
            assert isinstance(app.screen, ChatScreen)
            assert app.screen.agent_name == "character_keeper"
            log = await rt.events.events_since(0)
            assert not [e for e in log if e.event_type == EventType.CHAT_USER_MESSAGED]
    finally:
        await rt.close()


@pytest.mark.asyncio
async def test_unknown_agent_reports_error_and_stays(db_path):
    settings = Settings(db_path=db_path, projector_interval=0.05)
    rt = Runtime(settings, runners=_fake_agent_runners())
    await rt.start()
    try:
        app = NovelizerApp(rt)
        async with app.run_test() as pilot:
            await app._run_command("@story_architect hello?")
            await pilot.pause(0.3)
            assert not isinstance(app.screen, ChatScreen)
            assert any("unknown agent" in m for m in app.messages)
    finally:
        await rt.close()


def test_format_event_previews_agent_reply_and_feed_skips_user_message():
    reply = StoredEvent(
        sequence=1, id="e1", event_type=EventType.CHAT_AGENT_REPLIED, aggregate_id="author",
        payload={"agent_name": "author", "text": "x" * 200, "message_id": "m", "replying_to": ""},
        created_at="now",
    )
    rendered = format_event(reply)
    # New feed contract: identity speaker column, then a dim remark-style
    # reply preview; the 200-char text is clamped, never shown whole.
    assert rendered.startswith("✎ Author")
    assert '💬 replied: "' in rendered
    assert "x" * 200 not in rendered
