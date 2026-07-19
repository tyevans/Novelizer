import os
import tempfile
import pytest
from novelizer.settings import EffectiveSettings as Settings
from novelizer.runtime import Runtime
from novelizer.canon.events import EventType, SecretCreated
from novelizer.canon.autonomy import AutonomyLevel, AutonomyState
from novelizer.chat.schemas import ChatReply
from novelizer.agents.schemas import ThreadIntent, KnowledgeIntent
from novelizer.store.models import Chapter


class _R:
    def __init__(self, out):
        self._out = out
        self.calls = []

    async def ainvoke(self, inputs):
        self.calls.append(inputs)
        return {"structured_response": self._out}


class _Boom:
    async def ainvoke(self, inputs):
        raise RuntimeError("endpoint down")


async def _runtime(path, chat_runners):
    settings = Settings(db_path=path, projector_interval=0.05)
    rt = Runtime(settings, runners=chat_runners)
    await rt.start()
    return rt


@pytest.fixture
def db_path():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    os.unlink(path)


@pytest.mark.asyncio
async def test_send_then_reply_appends_both_events(db_path):
    runner = _R(ChatReply(reply_text="Deliberate? Then grief becomes foreshadowing."))
    rt = await _runtime(db_path, {"chat_author": runner})
    try:
        mid = await rt.chat.send("author", "what if the collapse was deliberate?")
        await rt.chat.generate_reply("author", replying_to=mid)
        log = await rt.events.events_since(0)
        user = [e for e in log if e.event_type == EventType.CHAT_USER_MESSAGED]
        reply = [e for e in log if e.event_type == EventType.CHAT_AGENT_REPLIED]
        assert len(user) == 1 and user[0].aggregate_id == "author"
        assert len(reply) == 1 and reply[0].payload["replying_to"] == mid
        assert "foreshadowing" in reply[0].payload["text"]
    finally:
        await rt.close()


@pytest.mark.asyncio
async def test_prompt_contains_history_and_latest_message(db_path):
    runner = _R(ChatReply(reply_text="ok"))
    rt = await _runtime(db_path, {"chat_author": runner})
    try:
        await rt.chat.send("author", "FIRST-QUESTION")
        await rt.chat.generate_reply("author")
        await rt.chat.send("author", "SECOND-QUESTION")
        await rt.chat.generate_reply("author")
        prompt = runner.calls[-1]["messages"][0]["content"]
        assert "FIRST-QUESTION" in prompt and "SECOND-QUESTION" in prompt
    finally:
        await rt.close()


@pytest.mark.asyncio
async def test_failed_generation_commits_nothing(db_path):
    rt = await _runtime(db_path, {"chat_author": _Boom()})
    try:
        await rt.chat.send("author", "hello?")
        with pytest.raises(RuntimeError):
            await rt.chat.generate_reply("author")
        log = await rt.events.events_since(0)
        assert not [e for e in log if e.event_type == EventType.CHAT_AGENT_REPLIED]
        assert not rt.chat.pending("author")
    finally:
        await rt.close()


@pytest.mark.asyncio
async def test_chat_intents_commit_with_chat_source(db_path):
    reply = ChatReply(reply_text="Planting it.", thread_intents=[ThreadIntent(action="plant", name="The Sealed Shaft")])
    rt = await _runtime(db_path, {"chat_author": _R(reply)})
    try:
        await rt.chat.send("author", "plant a thread about the shaft")
        await rt.chat.generate_reply("author")
        log = await rt.events.events_since(0)
        planted = [e for e in log if e.event_type == EventType.THREAD_PLANTED]
        assert len(planted) == 1
        assert planted[0].payload["source"] == "chat"
    finally:
        await rt.close()


@pytest.mark.asyncio
async def test_gated_intent_becomes_proposal(db_path):
    reply = ChatReply(reply_text="Revealing.", knowledge_intents=[KnowledgeIntent(action="reveal", id="s1")])
    rt = await _runtime(db_path, {"chat_author": _R(reply)})
    try:
        await rt.events.append(EventType.SECRET_CREATED, "s1", SecretCreated(id="s1", title="The Debt"))
        await rt.events.append(
            EventType.AUTONOMY_CHANGED, "singleton", AutonomyState(global_level=AutonomyLevel.gated_canon)
        )
        await rt.projector.catch_up()
        await rt.chat.send("author", "reveal the debt")
        await rt.chat.generate_reply("author")
        log = await rt.events.events_since(0)
        proposals = [e for e in log if e.event_type == EventType.PROPOSAL_CREATED]
        assert len(proposals) == 1
        assert proposals[0].payload["proposing_agent"] == "author"
        assert proposals[0].payload["target_event_type"] == EventType.SECRET_REVEALED
        assert not [e for e in log if e.event_type == EventType.SECRET_REVEALED]
    finally:
        await rt.close()


@pytest.mark.asyncio
async def test_story_context_default_mode_uses_recent_chapter_excerpts(db_path):
    rt = await _runtime(db_path, {"chat_author": _R(ChatReply(reply_text="ok"))})
    try:
        await rt.events.append(
            EventType.CHAPTER_CREATED, "c1",
            Chapter(id="c1", title="The Salt Road", prose="Once the tide receded, the road appeared."),
        )
        await rt.projector.catch_up()
        context = await rt.chat._story_context()
        assert "Recent chapters:" in context
        assert "Chapter index:" not in context
        assert "The Salt Road" in context
    finally:
        await rt.close()


@pytest.mark.asyncio
async def test_story_context_pull_mode_uses_chapter_index_no_prose_leak(db_path):
    rt = await _runtime(db_path, {"chat_author": _R(ChatReply(reply_text="ok"))})
    rt.chat.pull_mode = True
    try:
        await rt.events.append(
            EventType.CHAPTER_CREATED, "c1",
            Chapter(id="c1", title="The Salt Road", prose="SECRET-PROSE-TEXT should not leak."),
        )
        await rt.projector.catch_up()
        context = await rt.chat._story_context()
        assert "Chapter index:" in context
        assert "Recent chapters:" not in context
        assert "- [c1] 'The Salt Road' (draft) cast: none" in context
        assert "SECRET-PROSE-TEXT" not in context
    finally:
        await rt.close()


@pytest.mark.asyncio
async def test_persona_forbidden_intents_are_dropped(db_path):
    reply = ChatReply(reply_text="I should not plant.", thread_intents=[ThreadIntent(action="plant", name="Rogue Thread")])
    rt = await _runtime(db_path, {"chat_character_keeper": _R(reply)})
    try:
        await rt.chat.send("character_keeper", "plant something")
        await rt.chat.generate_reply("character_keeper")
        log = await rt.events.events_since(0)
        assert not [e for e in log if e.event_type == EventType.THREAD_PLANTED]
        assert [e for e in log if e.event_type == EventType.CHAT_AGENT_REPLIED]
    finally:
        await rt.close()
