from __future__ import annotations
import asyncio
import logging
import uuid
from typing import Callable
from novelizer.canon.events import EventType, ChatUserMessaged, ChatAgentReplied
from novelizer.canon.threads import active_thread_ids
from novelizer.agents.intents import (
    commit_thread_intents, commit_theme_intents, commit_knowledge_intents, commit_causal_intents,
)
from novelizer.chat.personas import CHAT_PERSONAS
from novelizer.chat.schemas import ChatReply
from novelizer.brain.context import chapter_map_note
from novelizer.brain.context_assembly import AdvisoryEntry, assemble_advisory
from novelizer.telemetry.recorder import run_with_identity

logger = logging.getLogger(__name__)

_HISTORY_LIMIT = 20  # most recent chat messages included in the prompt


class ChatReplyError(RuntimeError):
    """The runner returned no structured reply."""


def _transcript_block(history) -> str:
    lines = []
    for ev in history:
        who = "Director" if ev.event_type == EventType.CHAT_USER_MESSAGED else "You"
        lines.append(f"{who}: {ev.payload.get('text', '')}")
    return "\n".join(lines[-_HISTORY_LIMIT:]) or "(no conversation yet)"


class ChatService:
    """The chat bounded context's single entry point. UI-agnostic.

    send() appends the Director's message; generate_reply() invokes the
    agent's chat runner and appends the reply plus any permitted intents.
    Replies are serialized per agent (a second message queues behind an
    in-flight generation rather than racing it). History for the prompt is
    read from the event store, not the read model, so a reply generated
    immediately after send() can never miss its own user message to
    projection lag."""

    def __init__(
        self, events, read, committer, runner_for: Callable, personality_for: Callable[[str], str],
        pull_mode: bool = False, telemetry=None, advisory_token_budget: int = 2000,
    ) -> None:
        self._events = events
        self._read = read
        self._committer = committer
        self._runner_for = runner_for
        self._personality_for = personality_for
        self._locks: dict[str, asyncio.Lock] = {}
        self._pending: dict[str, int] = {}
        self.pull_mode = pull_mode
        self._telemetry = telemetry
        self.advisory_token_budget = advisory_token_budget

    def pending(self, agent_name: str) -> bool:
        return self._pending.get(agent_name, 0) > 0

    async def send(self, agent_name: str, text: str) -> str:
        message_id = str(uuid.uuid4())
        await self._events.append(
            EventType.CHAT_USER_MESSAGED, agent_name,
            ChatUserMessaged(message_id=message_id, agent_name=agent_name, text=text),
        )
        return message_id

    async def generate_reply(self, agent_name: str, replying_to: str = "") -> None:
        lock = self._locks.setdefault(agent_name, asyncio.Lock())
        self._pending[agent_name] = self._pending.get(agent_name, 0) + 1
        try:
            async with lock:
                prompt = await self._build_prompt(agent_name)
                runner = self._runner_for(agent_name)
                async with run_with_identity(self._telemetry, f"chat:{agent_name}"):
                    result = await runner.ainvoke({"messages": [{"role": "user", "content": prompt}]})
                    reply: ChatReply | None = result.get("structured_response")
                    if reply is None:
                        raise ChatReplyError(f"{agent_name} returned no structured reply")
                await self._events.append(
                    EventType.CHAT_AGENT_REPLIED, agent_name,
                    ChatAgentReplied(
                        message_id=str(uuid.uuid4()), agent_name=agent_name,
                        text=reply.reply_text, replying_to=replying_to,
                    ),
                )
                await self._commit_intents(agent_name, reply)
        finally:
            self._pending[agent_name] -= 1

    async def _build_prompt(self, agent_name: str) -> str:
        history = [
            ev for ev in await self._events.events_for_aggregate(agent_name)
            if ev.event_type in (EventType.CHAT_USER_MESSAGED, EventType.CHAT_AGENT_REPLIED)
        ]
        context = await self._story_context()
        persona_note = self._personality_for(agent_name)
        cast = f"\n\nIn character: {persona_note}" if persona_note else ""
        return (
            f"{context}{cast}\n\nConversation so far:\n{_transcript_block(history)}"
            "\n\nReply to the Director's latest message."
        )

    async def _story_context(self) -> str:
        world = await self._read.list_world_entries()
        characters = await self._read.list_characters()
        chapters = await self._read.list_chapters()
        threads = await self._read.list_threads()
        secrets = await self._read.list_secrets()
        themes = await self._read.list_themes()
        if self.pull_mode:
            # Titles and names only: tooled personas read canon themselves.
            # The thread/secret/theme id lists below stay -- the chat prompt
            # says "cite ids shown in the story context".
            w = "\n".join(f"- {e.title}" for e in world[:10]) or "None yet."
            c = "\n".join(f"- {ch.name}" for ch in characters[:8]) or "None yet."
        else:
            w = "\n".join(f"- {e.title}: {e.body[:150]}" for e in world[:10]) or "None yet."
            c = "\n".join(f"- {ch.name}: {ch.traits}" for ch in characters[:8]) or "None yet."
        t = "\n".join(f"- [{th.state.value}] {th.id}: {th.name}" for th in threads) or "None."
        s = "\n".join(
            f"- {sec.id}: {sec.title}" + (" (revealed)" if sec.revealed else "") for sec in secrets
        ) or "None."
        tm = "\n".join(f"- {th.id}: {th.title}" for th in themes) or "None."
        if self.pull_mode:
            summaries = await self._read.list_chapter_summaries()
            gists = {s.chapter_id: s.gist for s in summaries if s.gist}
            chapters_block = f"Chapter index:\n{chapter_map_note(chapters, gists=gists)}"
        else:
            summaries = await self._read.list_chapter_summaries()
            summaries_by_id = {s.chapter_id: s.summary for s in summaries}
            window = chapters[-3:]
            entries = [
                AdvisoryEntry(label=f"'{ch.title}'", summary=summaries_by_id.get(ch.id), verbatim=ch.prose)
                for ch in window
            ]
            prev = assemble_advisory(entries, self.advisory_token_budget) or "None yet."
            chapters_block = f"Recent chapters:\n{prev}"
        return (
            f"Story context.\nWorld lore:\n{w}\n\nCharacters:\n{c}\n\n{chapters_block}"
            f"\n\nThreads:\n{t}\n\nSecrets:\n{s}\n\nThemes:\n{tm}"
        )

    async def _commit_intents(self, agent_name: str, reply: ChatReply) -> None:
        persona = CHAT_PERSONAS[agent_name]
        if reply.thread_intents:
            if persona.allow_threads:
                threads = await self._read.list_threads()
                active = active_thread_ids(threads)
                await commit_thread_intents(self._committer, agent_name, reply.thread_intents, active, source="chat")
            else:
                logger.warning("%s: dropped %d thread intents not permitted in chat", agent_name, len(reply.thread_intents))
        if reply.theme_intents:
            if persona.allow_themes:
                active_themes = {t.id for t in await self._read.list_themes()}
                await commit_theme_intents(self._committer, agent_name, reply.theme_intents, active_themes, source="chat")
            else:
                logger.warning("%s: dropped %d theme intents not permitted in chat", agent_name, len(reply.theme_intents))
        if reply.knowledge_intents:
            if persona.knowledge_actions:
                active_secrets = {s.id for s in await self._read.list_secrets()}
                await commit_knowledge_intents(
                    self._committer, agent_name, reply.knowledge_intents, active_secrets,
                    allowed_actions=persona.knowledge_actions, source="chat",
                    character_ids={c.id for c in await self._read.list_characters()},
                )
            else:
                logger.warning("%s: dropped %d knowledge intents not permitted in chat", agent_name, len(reply.knowledge_intents))
        if reply.causal_intents:
            if persona.allow_causal:
                valid_chapters = {c.id for c in await self._read.list_chapters()}
                await commit_causal_intents(self._committer, agent_name, reply.causal_intents, valid_chapters, source="chat")
            else:
                logger.warning("%s: dropped %d causal intents not permitted in chat", agent_name, len(reply.causal_intents))
