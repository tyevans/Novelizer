# Agent Chat Tabs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Event-sourced, in-persona `@agent` chat conversations in the TUI, with replies that can carry intents flowing through the existing autonomy/proposal pipeline.

**Architecture:** New `novelizer/chat/` bounded context (personas, `ChatReply` schema, `ChatService`, chat runners) plus two new `chat.*` event types projected into a `chat_messages` read table. The seven agent classes are untouched except that `BaseAgent`'s `_commit_*` intent helpers are extracted into shared free functions used by both `BaseAgent` and `ChatService`. The TUI grows an `@name` command route, a full-screen `ChatScreen` with a tab strip, and async reply workers.

**Tech Stack:** Python 3.12+, uv, pydantic v2, aiosqlite, LangChain + deepagents (`create_deep_agent`), Textual 5.3, pytest + pytest-asyncio + Hypothesis.

**Spec:** `docs/superpowers/specs/2026-07-18-agent-chat-tabs-design.md`

## Global Constraints

- Run everything from the repo root with `uv run` (e.g. `uv run pytest tests/chat -q`). Never `pip install`.
- Red/green TDD: write the failing test, watch it fail, implement, watch it pass, commit. Never skip the fail run.
- Event sourcing rules: the event log is append-only; projections must be deterministic and idempotent; a failed chat generation commits **nothing** (the log never records a failure as speech).
- `chat.user_messaged` and `chat.agent_replied` are **never gated** (added to `_NEVER_GATED` in `novelizer/canon/policy.py`). Intents produced by chat replies go through `GatingCommitter` under the agent's own name and gate exactly like autonomous commits.
- All chat-sourced intent events carry `source="chat"`.
- Chat runners use `settings.agent_temperature`, `settings.llm_max_tokens` (4096 default), `settings.author_model` for the author and `settings.agent_model` for everyone else.
- One conversation per agent; the agent name IS the aggregate id.
- Async only — never block the Textual event loop on an LLM call.
- Commit messages follow the repo's existing style: `feat:`/`fix:`/`test:`/`refactor:` prefix plus a one-line rationale.
- **Spec deviation (approved during planning):** chat history for prompt assembly is read from the **event store** (`EventStore.events_for_aggregate`), not the read model — the read model lags the log by up to one projector interval (0.5 s), so a reply generated immediately after `send()` would race its own user message. The read model still drives the TUI transcript, exactly as specced.

## File Structure

| File | Responsibility |
| --- | --- |
| `novelizer/agents/intents.py` (new) | Free-function intent-commit helpers (validation/drop/downgrade rules), shared by `BaseAgent` and `ChatService` |
| `novelizer/agents/base.py` (modify) | `_commit_*` methods become thin delegates to `intents.py` |
| `novelizer/canon/events.py` (modify) | `CHAT_USER_MESSAGED` / `CHAT_AGENT_REPLIED` + payload models |
| `novelizer/canon/policy.py` (modify) | Chat events added to `_NEVER_GATED` |
| `novelizer/canon/projector.py` (modify) | `chat_messages` table + `_apply` branches + `_reset_state` entry |
| `novelizer/canon/event_store.py` (modify) | `events_for_aggregate(aggregate_id)` query |
| `novelizer/store/models.py` (modify) | `ChatMessageRecord` |
| `novelizer/canon/read_store.py` (modify) | `list_chat_messages`, `list_chat_conversations` |
| `novelizer/chat/__init__.py` (new) | Package marker |
| `novelizer/chat/personas.py` (new) | `ChatPersona` per agent (role blurb + intent permissions), aliases, `resolve_agent_name` |
| `novelizer/chat/schemas.py` (new) | `ChatReply` structured-output schema |
| `novelizer/chat/runners.py` (new) | `build_chat_runner(settings, agent_name)` |
| `novelizer/chat/service.py` (new) | `ChatService`: send, generate_reply, prompt assembly, intent commits |
| `novelizer/runtime.py` (modify) | Construct `ChatService`, lazy chat-runner cache, `chat_<name>` runner injection |
| `novelizer/tui/chat_screen.py` (new) | `ChatScreen`: tab strip, transcript, input, pending/unread, key bindings |
| `novelizer/tui/app.py` (modify) | `@name` routing, `send_chat_message`, reply worker, feed integration |

---

### Task 1: Extract intent-commit helpers into `novelizer/agents/intents.py`

The four `BaseAgent._commit_*` methods hold the drop/downgrade validation rules. `ChatService` needs the same rules without being an agent. Extract them as free functions; `BaseAgent` delegates so every existing agent test keeps exercising the shared code (that IS the refactor guard, plus new direct tests).

**Files:**
- Create: `novelizer/agents/intents.py`
- Modify: `novelizer/agents/base.py`
- Test: `tests/agents/test_intents.py`

**Interfaces:**
- Consumes: `Committer.commit(agent_name, event_type, aggregate_id, payload)`, event payload models from `novelizer/canon/events.py`, intent models from `novelizer/agents/schemas.py`.
- Produces (later tasks rely on these exact signatures):
  - `async def commit_thread_intents(committer, agent_name: str, intents: list[ThreadIntent], active_thread_ids: set[str], chapter_id: str = "", source: str = "declared") -> None`
  - `async def commit_theme_intents(committer, agent_name: str, intents: list[ThemeIntent], active_theme_ids: set[str], chapter_id: str = "", source: str = "declared") -> None`
  - `async def commit_knowledge_intents(committer, agent_name: str, intents: list[KnowledgeIntent], active_secret_ids: set[str], chapter_id: str = "", allowed_actions: frozenset[str] = frozenset({"plant", "learn", "reveal", "uses"}), source: str = "declared") -> None`
  - `async def commit_causal_intents(committer, agent_name: str, intents: list[CausalIntent], valid_chapter_ids: set[str], source: str = "declared") -> None`

- [ ] **Step 1: Write the failing test**

Create `tests/agents/test_intents.py`:

```python
import pytest
from novelizer.agents.intents import (
    commit_thread_intents, commit_theme_intents, commit_knowledge_intents, commit_causal_intents,
)
from novelizer.agents.schemas import ThreadIntent, ThemeIntent, KnowledgeIntent, CausalIntent
from novelizer.canon.events import EventType


class FakeCommitter:
    def __init__(self):
        self.commits = []

    async def commit(self, agent_name, event_type, aggregate_id, payload):
        self.commits.append((agent_name, event_type, aggregate_id, payload))


@pytest.mark.asyncio
async def test_thread_plant_mints_and_touch_requires_known_id():
    c = FakeCommitter()
    await commit_thread_intents(
        c, "author",
        [ThreadIntent(action="plant", name="The Broken Seal"),
         ThreadIntent(action="touch", id="nonexistent")],
        active_thread_ids=set(),
    )
    assert len(c.commits) == 1
    name, event_type, agg, payload = c.commits[0]
    assert (name, event_type) == ("author", EventType.THREAD_PLANTED)
    assert payload.id == "the-broken-seal"


@pytest.mark.asyncio
async def test_thread_plant_collision_downgrades_to_touch():
    c = FakeCommitter()
    await commit_thread_intents(
        c, "author",
        [ThreadIntent(action="plant", name="The Broken Seal")],
        active_thread_ids={"the-broken-seal"},
    )
    assert len(c.commits) == 1
    assert c.commits[0][1] == EventType.THREAD_TOUCHED


@pytest.mark.asyncio
async def test_source_is_threaded_through():
    c = FakeCommitter()
    await commit_thread_intents(
        c, "author", [ThreadIntent(action="touch", id="t1")],
        active_thread_ids={"t1"}, source="chat",
    )
    assert c.commits[0][3].source == "chat"


@pytest.mark.asyncio
async def test_knowledge_allowed_actions_restricts():
    c = FakeCommitter()
    await commit_knowledge_intents(
        c, "character_keeper",
        [KnowledgeIntent(action="reveal", id="s1"),
         KnowledgeIntent(action="learn", id="s1", character_id="c1")],
        active_secret_ids={"s1"},
        allowed_actions=frozenset({"learn"}),
    )
    assert len(c.commits) == 1
    assert c.commits[0][1] == EventType.SECRET_LEARNED


@pytest.mark.asyncio
async def test_theme_introduce_collision_downgrades_to_develop():
    c = FakeCommitter()
    await commit_theme_intents(
        c, "editor", [ThemeIntent(action="introduce", title="Grief")],
        active_theme_ids={"grief"},
    )
    assert len(c.commits) == 1
    assert c.commits[0][1] == EventType.THEME_DEVELOPED


@pytest.mark.asyncio
async def test_causal_drops_self_edge_and_unknown_ids():
    c = FakeCommitter()
    await commit_causal_intents(
        c, "editor",
        [CausalIntent(cause_chapter_id="ch1", effect_chapter_id="ch1"),
         CausalIntent(cause_chapter_id="ch1", effect_chapter_id="chX"),
         CausalIntent(cause_chapter_id="ch1", effect_chapter_id="ch2")],
        valid_chapter_ids={"ch1", "ch2"},
    )
    assert len(c.commits) == 1
    assert c.commits[0][1] == EventType.CAUSAL_EDGE_DECLARED
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/agents/test_intents.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'novelizer.agents.intents'`

- [ ] **Step 3: Create `novelizer/agents/intents.py` by moving the bodies out of `BaseAgent`**

Move the four method bodies from `novelizer/agents/base.py` verbatim, converting `self.name` → `agent_name` and `self._committer` → `committer`. The docstrings move with them. The result:

```python
from __future__ import annotations
import logging
from novelizer.canon.events import (
    EventType, ThreadPlanted, ThreadTouched, ThreadPaidOff, ThreadAbandoned,
    SecretCreated, SecretLearned, SecretReferenced, SecretRevealed, CausalEdgeDeclared,
    ThemeIntroduced, ThemeDeveloped,
)
from novelizer.canon.threads import slugify_thread_name
from novelizer.canon.secrets import slugify_secret_name
from novelizer.canon.themes import slugify_theme_name
from novelizer.agents.schemas import ThreadIntent, KnowledgeIntent, CausalIntent, ThemeIntent

logger = logging.getLogger(__name__)

_KNOWLEDGE_EVENT_BY_ACTION = {
    "learn": (EventType.SECRET_LEARNED, SecretLearned),
    "reveal": (EventType.SECRET_REVEALED, SecretRevealed),
    "uses": (EventType.SECRET_REFERENCED, SecretReferenced),
}


async def commit_thread_intents(
    committer,
    agent_name: str,
    intents: list[ThreadIntent],
    active_thread_ids: set[str],
    chapter_id: str = "",
    source: str = "declared",
) -> None:
    # (docstring moved verbatim from BaseAgent._commit_thread_intents)
    for intent in intents:
        if intent.action == "plant":
            if not intent.name.strip():
                logger.warning("%s: dropped thread plant intent with empty name", agent_name)
                continue
            thread_id = slugify_thread_name(intent.name)
            if thread_id in active_thread_ids:
                logger.info(
                    "%s: plant %r collides with active thread id %r, downgrading to touch",
                    agent_name, intent.name, thread_id,
                )
                await committer.commit(
                    agent_name, EventType.THREAD_TOUCHED, thread_id,
                    ThreadTouched(id=thread_id, chapter_id=chapter_id, note=intent.note, source=source),
                )
                continue
            logger.warning(
                "%s: plant %r mints id %r; if this id already exists (terminal or unknown "
                "to the caller) the commit will be a projection no-op",
                agent_name, intent.name, thread_id,
            )
            await committer.commit(
                agent_name, EventType.THREAD_PLANTED, thread_id,
                ThreadPlanted(id=thread_id, name=intent.name, chapter_id=chapter_id, note=intent.note, source=source),
            )
            continue
        if intent.id not in active_thread_ids:
            logger.warning(
                "%s: dropped thread %s intent for unknown id %r", agent_name, intent.action, intent.id
            )
            continue
        payload_cls, event_type = {
            "touch": (ThreadTouched, EventType.THREAD_TOUCHED),
            "pay_off": (ThreadPaidOff, EventType.THREAD_PAID_OFF),
            "abandon": (ThreadAbandoned, EventType.THREAD_ABANDONED),
        }[intent.action]
        if payload_cls is ThreadAbandoned:
            payload = payload_cls(id=intent.id, chapter_id=chapter_id, note=intent.note)
        else:
            payload = payload_cls(id=intent.id, chapter_id=chapter_id, note=intent.note, source=source)
        await committer.commit(agent_name, event_type, intent.id, payload)
```

Repeat the same mechanical transformation for `commit_theme_intents`, `commit_knowledge_intents` (keep the `allowed_actions` parameter and default), and `commit_causal_intents` — the bodies in `base.py` lines 155–320 move verbatim with `self.name` → `agent_name`, `self._committer` → `committer`.

- [ ] **Step 4: Make `BaseAgent` delegate**

In `novelizer/agents/base.py`, replace each `_commit_*` method body with a delegate (keep the method names and signatures so all seven agents are untouched):

```python
from novelizer.agents import intents as intent_helpers

    async def _commit_thread_intents(self, intents, active_thread_ids, chapter_id="", source="declared") -> None:
        await intent_helpers.commit_thread_intents(
            self._committer, self.name, intents, active_thread_ids, chapter_id=chapter_id, source=source
        )

    async def _commit_theme_intents(self, intents, active_theme_ids, chapter_id="", source="declared") -> None:
        await intent_helpers.commit_theme_intents(
            self._committer, self.name, intents, active_theme_ids, chapter_id=chapter_id, source=source
        )

    async def _commit_knowledge_intents(
        self, intents, active_secret_ids, chapter_id="",
        allowed_actions=frozenset({"plant", "learn", "reveal", "uses"}), source="declared",
    ) -> None:
        await intent_helpers.commit_knowledge_intents(
            self._committer, self.name, intents, active_secret_ids, chapter_id=chapter_id,
            allowed_actions=allowed_actions, source=source,
        )

    async def _commit_causal_intents(self, intents, valid_chapter_ids, source="declared") -> None:
        await intent_helpers.commit_causal_intents(
            self._committer, self.name, intents, valid_chapter_ids, source=source
        )
```

Remove the now-unused imports from `base.py` (`slugify_*`, the payload classes, `_KNOWLEDGE_EVENT_BY_ACTION`) — keep `AgentRemark`, `EventType`.

- [ ] **Step 5: Run tests to verify pass, including the full suite as refactor guard**

Run: `uv run pytest tests/agents/test_intents.py -q` → PASS
Run: `uv run pytest -q` → all existing tests PASS (the delegation means `tests/agents/test_base.py` and every agent test now exercise the shared helpers)

- [ ] **Step 6: Commit**

```bash
git add novelizer/agents/intents.py novelizer/agents/base.py tests/agents/test_intents.py
git commit -m "refactor: extract intent-commit helpers from BaseAgent into agents/intents.py — one rulebook for autonomous and chat commits"
```

---

### Task 2: Chat domain — events, never-gated policy, projection, read model

**Files:**
- Modify: `novelizer/canon/events.py`
- Modify: `novelizer/canon/policy.py`
- Modify: `novelizer/canon/projector.py`
- Modify: `novelizer/store/models.py`
- Modify: `novelizer/canon/read_store.py`
- Test: `tests/canon/test_chat_projection.py`, `tests/canon/test_chat_projection_property.py`

**Interfaces:**
- Produces:
  - `EventType.CHAT_USER_MESSAGED = "chat.user_messaged"`, `EventType.CHAT_AGENT_REPLIED = "chat.agent_replied"`
  - `class ChatUserMessaged(BaseModel): message_id: str; agent_name: str; text: str`
  - `class ChatAgentReplied(BaseModel): message_id: str; agent_name: str; text: str; replying_to: str = ""`
  - `class ChatMessageRecord(BaseModel): agent_name: str; role: str; text: str; message_id: str` (in `novelizer/store/models.py`)
  - `ReadStore.list_chat_messages(agent_name: str, limit: int = 200) -> list[ChatMessageRecord]` (oldest→newest)
  - `ReadStore.list_chat_conversations() -> list[str]` (sorted agent names with ≥1 message)

- [ ] **Step 1: Write the failing unit test**

Create `tests/canon/test_chat_projection.py`:

```python
import os
import tempfile
import pytest
from novelizer.canon.event_store import EventStore
from novelizer.canon.projector import Projector
from novelizer.canon.read_store import ReadStore
from novelizer.canon.events import EventType, ChatUserMessaged, ChatAgentReplied
from novelizer.canon.policy import _NEVER_GATED


@pytest.fixture
def db_path():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    os.unlink(path)


async def _stores(path):
    events = EventStore(path)
    await events.init()
    proj = Projector(events, path)
    await proj.init()
    read = ReadStore(path)
    await read.init()
    return events, proj, read


@pytest.mark.asyncio
async def test_chat_events_project_to_ordered_transcript(db_path):
    events, proj, read = await _stores(db_path)
    await events.append(
        EventType.CHAT_USER_MESSAGED, "author",
        ChatUserMessaged(message_id="m1", agent_name="author", text="what if the mine collapse was deliberate?"),
    )
    await events.append(
        EventType.CHAT_AGENT_REPLIED, "author",
        ChatAgentReplied(message_id="m2", agent_name="author", text="Then ch. 3 reads as foreshadowing.", replying_to="m1"),
    )
    await events.append(
        EventType.CHAT_USER_MESSAGED, "editor",
        ChatUserMessaged(message_id="m3", agent_name="editor", text="too slow?"),
    )
    await proj.catch_up()
    msgs = await read.list_chat_messages("author")
    assert [(m.role, m.message_id) for m in msgs] == [("user", "m1"), ("agent", "m2")]
    assert msgs[0].text.startswith("what if")
    assert await read.list_chat_conversations() == ["author", "editor"]


@pytest.mark.asyncio
async def test_chat_projection_is_idempotent_per_message_id(db_path):
    events, proj, read = await _stores(db_path)
    await events.append(
        EventType.CHAT_USER_MESSAGED, "author",
        ChatUserMessaged(message_id="m1", agent_name="author", text="hello"),
    )
    await proj.catch_up()
    await proj._reset_state()
    await proj.catch_up()
    msgs = await read.list_chat_messages("author")
    assert len(msgs) == 1


def test_chat_events_are_never_gated():
    assert EventType.CHAT_USER_MESSAGED in _NEVER_GATED
    assert EventType.CHAT_AGENT_REPLIED in _NEVER_GATED
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/canon/test_chat_projection.py -q`
Expected: FAIL — `ImportError: cannot import name 'ChatUserMessaged'`

- [ ] **Step 3: Implement the domain pieces**

`novelizer/canon/events.py` — add to `EventType`:

```python
    CHAT_USER_MESSAGED = "chat.user_messaged"
    CHAT_AGENT_REPLIED = "chat.agent_replied"
```

and add payload models at the end of the file:

```python
class ChatUserMessaged(BaseModel):
    """Payload for chat.user_messaged — the Director speaks to one agent.

    One conversation per agent: the event's aggregate_id is the agent name.
    Never gated (chat is a first-person channel, not an agent canon write).
    """

    message_id: str
    agent_name: str
    text: str


class ChatAgentReplied(BaseModel):
    """Payload for chat.agent_replied — an agent's completed chat reply.

    Committed only when generation completes; a failed generation commits
    nothing (the log never records a failure as speech). `replying_to` cites
    the chat.user_messaged message_id that prompted this reply.
    """

    message_id: str
    agent_name: str
    text: str
    replying_to: str = ""
```

`novelizer/canon/policy.py` — add both to `_NEVER_GATED`:

```python
    EventType.CHAT_USER_MESSAGED,
    EventType.CHAT_AGENT_REPLIED,
```

`novelizer/store/models.py` — add:

```python
class ChatMessageRecord(BaseModel):
    """One projected chat message. role is 'user' (the Director) or 'agent'."""

    agent_name: str
    role: str
    text: str
    message_id: str
```

`novelizer/canon/projector.py` — add to `_CREATE`:

```sql
CREATE TABLE IF NOT EXISTS chat_messages (
    message_id TEXT PRIMARY KEY, agent_name TEXT NOT NULL, role TEXT NOT NULL, text TEXT NOT NULL
);
```

add `"chat_messages"` to the `_reset_state` table tuple, and add an `_apply` branch (before the final `AUTONOMY_CHANGED` branch):

```python
        elif t == EventType.CHAT_USER_MESSAGED or t == EventType.CHAT_AGENT_REPLIED:
            role = "user" if t == EventType.CHAT_USER_MESSAGED else "agent"
            await self._conn.execute(
                "INSERT OR IGNORE INTO chat_messages (message_id, agent_name, role, text) VALUES (?,?,?,?)",
                (p["message_id"], p["agent_name"], role, p.get("text", "")),
            )
```

`novelizer/canon/read_store.py` — import `ChatMessageRecord` and add:

```python
    async def list_chat_messages(self, agent_name: str, limit: int = 200) -> list[ChatMessageRecord]:
        cur = await self._conn.execute(
            "SELECT agent_name, role, text, message_id FROM chat_messages "
            "WHERE agent_name=? ORDER BY rowid DESC LIMIT ?",
            (agent_name, limit),
        )
        rows = list(await cur.fetchall())[::-1]
        return [
            ChatMessageRecord(agent_name=r[0], role=r[1], text=r[2], message_id=r[3]) for r in rows
        ]

    async def list_chat_conversations(self) -> list[str]:
        cur = await self._conn.execute(
            "SELECT DISTINCT agent_name FROM chat_messages ORDER BY agent_name"
        )
        return [r[0] for r in await cur.fetchall()]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/canon/test_chat_projection.py -q`
Expected: PASS

- [ ] **Step 5: Write the failing property test (projection determinism)**

Create `tests/canon/test_chat_projection_property.py`:

```python
import asyncio
import os
import tempfile
from hypothesis import given, settings, strategies as st
from novelizer.canon.event_store import EventStore
from novelizer.canon.projector import Projector
from novelizer.canon.read_store import ReadStore
from novelizer.canon.events import EventType, ChatUserMessaged, ChatAgentReplied

AGENTS = ["author", "editor", "character_keeper"]

message_strategy = st.lists(
    st.tuples(st.sampled_from(AGENTS), st.sampled_from(["user", "agent"]), st.text(max_size=40)),
    max_size=30,
)


async def _project(seq: list[tuple[str, str, str]]) -> dict[str, list[tuple[str, str]]]:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        events = EventStore(path)
        await events.init()
        proj = Projector(events, path)
        await proj.init()
        read = ReadStore(path)
        await read.init()
        for i, (agent, role, text) in enumerate(seq):
            mid = f"m{i}"
            if role == "user":
                await events.append(
                    EventType.CHAT_USER_MESSAGED, agent,
                    ChatUserMessaged(message_id=mid, agent_name=agent, text=text),
                )
            else:
                await events.append(
                    EventType.CHAT_AGENT_REPLIED, agent,
                    ChatAgentReplied(message_id=mid, agent_name=agent, text=text),
                )
        await proj.catch_up()
        out = {}
        for agent in AGENTS:
            msgs = await read.list_chat_messages(agent)
            out[agent] = [(m.role, m.message_id) for m in msgs]
        await read.close()
        await proj.close()
        await events.close()
        return out
    finally:
        os.unlink(path)


@settings(max_examples=25, deadline=None)
@given(message_strategy)
def test_any_interleaving_projects_per_agent_transcripts_in_order(seq):
    """Per-agent transcript == that agent's subsequence of the log, order preserved."""
    projected = asyncio.run(_project(seq))
    for agent in AGENTS:
        expected = [(role, f"m{i}") for i, (a, role, _t) in enumerate(seq) if a == agent]
        assert projected[agent] == expected
```

- [ ] **Step 6: Run the property test**

Run: `uv run pytest tests/canon/test_chat_projection_property.py -q`
Expected: PASS (the Step 3 implementation should already satisfy it; if it fails, the projection has an ordering bug — fix the projection, not the test)

- [ ] **Step 7: Run full suite and commit**

Run: `uv run pytest -q` → PASS

```bash
git add novelizer/canon/events.py novelizer/canon/policy.py novelizer/canon/projector.py novelizer/store/models.py novelizer/canon/read_store.py tests/canon/test_chat_projection.py tests/canon/test_chat_projection_property.py
git commit -m "feat: chat.* events, never-gated policy, chat_messages projection + read model"
```

---

### Task 3: Chat personas, `ChatReply` schema, agent-name resolution

**Files:**
- Create: `novelizer/chat/__init__.py` (empty)
- Create: `novelizer/chat/personas.py`
- Create: `novelizer/chat/schemas.py`
- Test: `tests/chat/__init__.py` (empty), `tests/chat/test_personas.py`

**Interfaces:**
- Produces:
  - `class ChatPersona(BaseModel)`: fields `role_prompt: str`, `allow_threads: bool`, `allow_themes: bool`, `allow_causal: bool`, `knowledge_actions: frozenset[str]`
  - `CHAT_PERSONAS: dict[str, ChatPersona]` — keys are the seven canonical agent names
  - `resolve_agent_name(token: str) -> str | None`
  - `class ChatReply(BaseModel)`: `reply_text: str`, plus `thread_intents`, `knowledge_intents`, `causal_intents`, `theme_intents` lists defaulting empty

- [ ] **Step 1: Write the failing test**

Create `tests/chat/test_personas.py` (and empty `tests/chat/__init__.py`):

```python
from novelizer.chat.personas import CHAT_PERSONAS, resolve_agent_name
from novelizer.chat.schemas import ChatReply

CANONICAL = {
    "author", "editor", "world_architect", "character_keeper",
    "continuity_checker", "retconner", "structure_analyst",
}


def test_personas_cover_exactly_the_seven_agents():
    assert set(CHAT_PERSONAS) == CANONICAL
    for persona in CHAT_PERSONAS.values():
        assert persona.role_prompt.strip()


def test_intent_permissions_mirror_autonomous_behavior():
    assert CHAT_PERSONAS["author"].knowledge_actions == frozenset({"plant", "learn", "reveal", "uses"})
    assert CHAT_PERSONAS["editor"].knowledge_actions == frozenset({"plant", "learn", "reveal", "uses"})
    assert CHAT_PERSONAS["character_keeper"].knowledge_actions == frozenset({"learn"})
    for name in ("world_architect", "continuity_checker", "retconner", "structure_analyst"):
        p = CHAT_PERSONAS[name]
        assert not p.allow_threads and not p.allow_themes and not p.allow_causal
        assert p.knowledge_actions == frozenset()
    assert CHAT_PERSONAS["author"].allow_threads and CHAT_PERSONAS["author"].allow_causal
    assert not CHAT_PERSONAS["character_keeper"].allow_threads


def test_resolve_agent_name_canonical_aliases_and_case():
    assert resolve_agent_name("author") == "author"
    assert resolve_agent_name("Keeper") == "character_keeper"
    assert resolve_agent_name("architect") == "world_architect"
    assert resolve_agent_name("continuity") == "continuity_checker"
    assert resolve_agent_name("analyst") == "structure_analyst"
    assert resolve_agent_name("retcon") == "retconner"
    assert resolve_agent_name("story_architect") is None
    assert resolve_agent_name("") is None


def test_chat_reply_defaults_are_empty_intents():
    r = ChatReply(reply_text="hi")
    assert r.thread_intents == [] and r.knowledge_intents == []
    assert r.causal_intents == [] and r.theme_intents == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/chat -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'novelizer.chat'`

- [ ] **Step 3: Implement**

`novelizer/chat/schemas.py`:

```python
from __future__ import annotations
from pydantic import BaseModel, Field
from novelizer.agents.schemas import ThreadIntent, KnowledgeIntent, CausalIntent, ThemeIntent


class ChatReply(BaseModel):
    """Structured output for one chat reply: in-character prose plus optional
    intents. Intents are validated and permission-filtered by ChatService
    against the agent's ChatPersona before any commit."""

    reply_text: str
    thread_intents: list[ThreadIntent] = Field(default_factory=list)
    knowledge_intents: list[KnowledgeIntent] = Field(default_factory=list)
    causal_intents: list[CausalIntent] = Field(default_factory=list)
    theme_intents: list[ThemeIntent] = Field(default_factory=list)
```

`novelizer/chat/personas.py`:

```python
from __future__ import annotations
from pydantic import BaseModel

_FULL_KNOWLEDGE = frozenset({"plant", "learn", "reveal", "uses"})
_NO_KNOWLEDGE: frozenset[str] = frozenset()


class ChatPersona(BaseModel):
    """Per-agent chat configuration: how the agent presents itself in
    conversation and which intent families it may commit from chat.
    Permissions mirror what the agent may do autonomously — chat is not a
    privilege-escalation path."""

    model_config = {"frozen": True}

    role_prompt: str
    allow_threads: bool = False
    allow_themes: bool = False
    allow_causal: bool = False
    knowledge_actions: frozenset[str] = _NO_KNOWLEDGE


CHAT_PERSONAS: dict[str, ChatPersona] = {
    "author": ChatPersona(
        role_prompt="You are the Author — you write the chapters. You think in scenes, beats, and consequences.",
        allow_threads=True, allow_themes=True, allow_causal=True, knowledge_actions=_FULL_KNOWLEDGE,
    ),
    "editor": ChatPersona(
        role_prompt="You are the Editor — you review chapters for quality, pacing, and voice.",
        allow_threads=True, allow_themes=True, allow_causal=True, knowledge_actions=_FULL_KNOWLEDGE,
    ),
    "world_architect": ChatPersona(
        role_prompt="You are the World Architect — you tend the lore, places, systems, and rules of the world.",
    ),
    "character_keeper": ChatPersona(
        role_prompt="You are the Character Keeper — you track every character's arc, traits, and knowledge.",
        knowledge_actions=frozenset({"learn"}),
    ),
    "continuity_checker": ChatPersona(
        role_prompt="You are the Continuity Checker — you hunt contradictions, leaks, and drift across the manuscript.",
    ),
    "retconner": ChatPersona(
        role_prompt="You are the Retconner — you resolve approved retcons by amending lore cleanly.",
    ),
    "structure_analyst": ChatPersona(
        role_prompt="You are the Structure Analyst — you read the manuscript's tension curve and pacing.",
    ),
}

_ALIASES = {
    "keeper": "character_keeper",
    "architect": "world_architect",
    "continuity": "continuity_checker",
    "analyst": "structure_analyst",
    "structure": "structure_analyst",
    "retcon": "retconner",
}


def resolve_agent_name(token: str) -> str | None:
    """Resolve an @-mention token to a canonical agent name, or None."""
    key = token.strip().lower()
    if key in CHAT_PERSONAS:
        return key
    return _ALIASES.get(key)
```

Create empty `novelizer/chat/__init__.py` and `tests/chat/__init__.py`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/chat -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add novelizer/chat tests/chat
git commit -m "feat: chat personas with autonomy-mirroring intent permissions, ChatReply schema, @-name resolution"
```

---

### Task 4: `ChatService`, chat runners, event-store history query, Runtime wiring

**Files:**
- Modify: `novelizer/canon/event_store.py`
- Create: `novelizer/chat/runners.py`
- Create: `novelizer/chat/service.py`
- Modify: `novelizer/runtime.py`
- Test: `tests/chat/test_service.py`

**Interfaces:**
- Consumes: Task 1 helpers, Task 2 events/read model, Task 3 personas/schemas; `GatingCommitter.commit`; `EventStore.append`.
- Produces:
  - `EventStore.events_for_aggregate(aggregate_id: str) -> list[StoredEvent]` (ascending sequence)
  - `build_chat_runner(settings, agent_name: str)` → deepagents runner with `response_format=ChatReply`
  - `class ChatService`:
    - `__init__(self, events, read, committer, runner_for, personality_for)` — `runner_for: Callable[[str], Runner]`, `personality_for: Callable[[str], str]`
    - `async def send(self, agent_name: str, text: str) -> str` (returns message_id)
    - `async def generate_reply(self, agent_name: str, replying_to: str = "") -> None` (raises on failure; commits nothing on failure)
    - `def pending(self, agent_name: str) -> bool`
  - `Runtime.chat: ChatService` (constructed in `start()`); injected fakes via `runners={"chat_<agent_name>": fake}`

- [ ] **Step 1: Write the failing test**

Create `tests/chat/test_service.py`:

```python
import os
import tempfile
import pytest
from novelizer.settings import EffectiveSettings as Settings
from novelizer.runtime import Runtime
from novelizer.canon.events import EventType, SecretCreated, ThreadPlanted
from novelizer.canon.autonomy import AutonomyLevel, AutonomyState
from novelizer.chat.schemas import ChatReply
from novelizer.agents.schemas import ThreadIntent, KnowledgeIntent


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/chat/test_service.py -q`
Expected: FAIL — `AttributeError: 'Runtime' object has no attribute 'chat'` (or import error for `novelizer.chat.service`)

- [ ] **Step 3: Implement `EventStore.events_for_aggregate`**

In `novelizer/canon/event_store.py`, alongside `events_since`:

```python
    async def events_for_aggregate(self, aggregate_id: str) -> list[StoredEvent]:
        cur = await self._conn.execute(
            f"SELECT {_COLS} FROM events WHERE aggregate_id=? ORDER BY sequence",
            (aggregate_id,),
        )
        return [_row_to_event(r) for r in await cur.fetchall()]
```

- [ ] **Step 4: Implement `novelizer/chat/runners.py`**

```python
from __future__ import annotations
from novelizer.chat.personas import CHAT_PERSONAS
from novelizer.chat.schemas import ChatReply

CHAT_SYSTEM_PROMPT = """{role_prompt}
You are in a private consultation with the Director of this living fictional world.
Answer the Director's latest message in character: concrete, specific to this story, and brief.
You may optionally declare intents (threads, secrets, causal edges, themes) when the
conversation genuinely warrants a real change to the story record; otherwise leave every
intent list empty. Never invent ids — cite ids shown in the story context, or use the
minting action (plant/introduce) with a name."""


def build_chat_runner(settings, agent_name: str):
    from deepagents import create_deep_agent
    from novelizer.agents.llm import build_chat_model
    model_name = settings.author_model if agent_name == "author" else settings.agent_model
    model = build_chat_model(
        model_name, settings.llm_base_url, settings.llm_api_key,
        settings.agent_temperature, max_tokens=settings.llm_max_tokens,
    )
    persona = CHAT_PERSONAS[agent_name]
    return create_deep_agent(
        model=model,
        system_prompt=CHAT_SYSTEM_PROMPT.format(role_prompt=persona.role_prompt),
        response_format=ChatReply,
    )
```

- [ ] **Step 5: Implement `novelizer/chat/service.py`**

```python
from __future__ import annotations
import asyncio
import logging
import uuid
from typing import Callable
from novelizer.canon.events import EventType, ChatUserMessaged, ChatAgentReplied
from novelizer.canon.threads import TERMINAL_STATES
from novelizer.agents.intents import (
    commit_thread_intents, commit_theme_intents, commit_knowledge_intents, commit_causal_intents,
)
from novelizer.chat.personas import CHAT_PERSONAS
from novelizer.chat.schemas import ChatReply

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

    def __init__(self, events, read, committer, runner_for: Callable, personality_for: Callable[[str], str]) -> None:
        self._events = events
        self._read = read
        self._committer = committer
        self._runner_for = runner_for
        self._personality_for = personality_for
        self._locks: dict[str, asyncio.Lock] = {}
        self._pending: dict[str, int] = {}

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
        w = "\n".join(f"- {e.title}: {e.body[:150]}" for e in world[:10]) or "None yet."
        c = "\n".join(f"- {ch.name}: {ch.traits}" for ch in characters[:8]) or "None yet."
        prev = "\n".join(f"- '{ch.title}': {ch.prose[:200]}" for ch in chapters[-3:]) or "None yet."
        t = "\n".join(f"- [{th.state.value}] {th.id}: {th.name}" for th in threads) or "None."
        s = "\n".join(
            f"- {sec.id}: {sec.title}" + (" (revealed)" if sec.revealed else "") for sec in secrets
        ) or "None."
        tm = "\n".join(f"- {th.id}: {th.title}" for th in themes) or "None."
        return (
            f"Story context.\nWorld lore:\n{w}\n\nCharacters:\n{c}\n\nRecent chapters:\n{prev}"
            f"\n\nThreads:\n{t}\n\nSecrets:\n{s}\n\nThemes:\n{tm}"
        )

    async def _commit_intents(self, agent_name: str, reply: ChatReply) -> None:
        persona = CHAT_PERSONAS[agent_name]
        if reply.thread_intents:
            if persona.allow_threads:
                threads = await self._read.list_threads()
                active = {t.id for t in threads if t.state.value not in TERMINAL_STATES}
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
                )
            else:
                logger.warning("%s: dropped %d knowledge intents not permitted in chat", agent_name, len(reply.knowledge_intents))
        if reply.causal_intents:
            if persona.allow_causal:
                valid_chapters = {c.id for c in await self._read.list_chapters()}
                await commit_causal_intents(self._committer, agent_name, reply.causal_intents, valid_chapters, source="chat")
            else:
                logger.warning("%s: dropped %d causal intents not permitted in chat", agent_name, len(reply.causal_intents))
```

- [ ] **Step 6: Wire into `Runtime`**

In `novelizer/runtime.py`: add imports

```python
from novelizer.chat.service import ChatService
from novelizer.chat.runners import build_chat_runner
```

add to `__init__`:

```python
        self.chat: Optional[ChatService] = None
        self._chat_runner_cache: dict[str, object] = {}
```

add a method:

```python
    def _chat_runner_for(self, agent_name: str):
        """Lazy per-agent chat runner. Injected fakes use key 'chat_<name>' in
        the runners dict; real runners are built on first use and cached."""
        key = f"chat_{agent_name}"
        if self._runners is not None and key in self._runners:
            return self._runners[key]
        if key not in self._chat_runner_cache:
            self._chat_runner_cache[key] = build_chat_runner(self.settings, agent_name)
        return self._chat_runner_cache[key]
```

and at the end of `start()` (after `self.scheduler = Scheduler(...)`):

```python
        self.chat = ChatService(
            self.events, self.read, self.committer, self._chat_runner_for,
            lambda name: self.voice_pack.agent_personalities.get(name, ""),
        )
```

In `apply_settings`, invalidate cached chat runners when temperatures change (alongside the existing rebuild block):

```python
        if ("agent_temperature" in changed or "author_temperature" in changed) and rebuild:
            self._chat_runner_cache.clear()
```

- [ ] **Step 7: Run tests to verify pass**

Run: `uv run pytest tests/chat -q` → PASS
Run: `uv run pytest -q` → full suite PASS

- [ ] **Step 8: Commit**

```bash
git add novelizer/canon/event_store.py novelizer/chat/runners.py novelizer/chat/service.py novelizer/runtime.py tests/chat/test_service.py
git commit -m "feat: ChatService — event-sourced chat replies with persona-gated intents through GatingCommitter (source=chat)"
```

---

### Task 5: `ChatScreen` — tab strip, transcript, input, pending/unread

**Files:**
- Create: `novelizer/tui/chat_screen.py`
- Modify: `novelizer/tui/app.tcss` (append styles)
- Test: `tests/tui/test_chat_screen.py`

**Interfaces:**
- Consumes: `runtime.read.list_chat_messages` / `list_chat_conversations`, `runtime.chat.pending`, `app.send_chat_message(agent_name, text)` (defined in Task 6 — this task adds it as a thin stub on the app for the screen to call; Task 6 completes it. To keep this task independently testable, the screen calls `self.app.send_chat_message(...)` and the test provides it).
- Produces:
  - `class ChatScreen(Screen)`: `__init__(self, runtime, agent_name: str)`, `set_current(agent_name: str)`, `add_error(agent_name: str, line: str)`
  - Bindings: `escape` → pop screen, `ctrl+pageup`/`ctrl+pagedown` → cycle conversations
  - Widget ids: `#chat_tabs` (Tabs), `#chat_log` (RichLog), `#chat_input` (Input); tab ids `chat-<agent_name>`

- [ ] **Step 1: Write the failing test**

Create `tests/tui/test_chat_screen.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/tui/test_chat_screen.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'novelizer.tui.chat_screen'`

- [ ] **Step 3: Implement `novelizer/tui/chat_screen.py`**

```python
from __future__ import annotations
import asyncio
from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Footer, Input, RichLog, Tab, Tabs

_POLL_INTERVAL = 0.5


class ChatScreen(Screen):
    """Full-screen chat with one agent, with a tab strip over every existing
    conversation. Transcript state comes from the read model; pending and
    unread are session-only UI state."""

    BINDINGS = [
        ("escape", "back", "Mission Control"),
        ("ctrl+pageup", "prev_chat", "Prev chat"),
        ("ctrl+pagedown", "next_chat", "Next chat"),
    ]

    def __init__(self, runtime, agent_name: str) -> None:
        super().__init__()
        self.runtime = runtime
        self.agent_name = agent_name
        self._agents: list[str] = [agent_name]
        self._seen: dict[str, int] = {}
        self._errors: dict[str, list[str]] = {}
        self._last_render_key: tuple = ()

    def compose(self) -> ComposeResult:
        yield Tabs(Tab(f"@{self.agent_name}", id=f"chat-{self.agent_name}"), id="chat_tabs")
        yield RichLog(highlight=False, markup=False, id="chat_log")
        yield Input(id="chat_input", placeholder=f"message @{self.agent_name}…", compact=True)
        yield Footer()

    async def on_mount(self) -> None:
        self.run_worker(self._poll_loop(), exclusive=False)
        self.set_focus(self.query_one("#chat_input", Input))

    # -- public API used by the app --------------------------------------

    def set_current(self, agent_name: str) -> None:
        """Switch the screen to another agent's conversation (used by @mention
        routing while the screen is already open)."""
        self.agent_name = agent_name
        if agent_name not in self._agents:
            self._agents.append(agent_name)
        self._sync_tabs()
        tabs = self.query_one("#chat_tabs", Tabs)
        tabs.active = f"chat-{agent_name}"
        self.query_one("#chat_input", Input).placeholder = f"message @{agent_name}…"

    def add_error(self, agent_name: str, line: str) -> None:
        self._errors.setdefault(agent_name, []).append(line)
        self._last_render_key = ()  # force re-render on next poll

    # -- internals ---------------------------------------------------------

    def _sync_tabs(self) -> None:
        tabs = self.query_one("#chat_tabs", Tabs)
        existing = {t.id for t in tabs.query(Tab)}
        for agent in self._agents:
            tab_id = f"chat-{agent}"
            if tab_id not in existing:
                tabs.add_tab(Tab(f"@{agent}", id=tab_id))

    def _tab_label(self, agent: str, count: int) -> str:
        unread = agent != self.agent_name and count > self._seen.get(agent, 0)
        return f"@{agent} ●" if unread else f"@{agent}"

    async def _poll_loop(self) -> None:
        while True:
            try:
                await self._refresh()
            except Exception:
                pass
            await asyncio.sleep(_POLL_INTERVAL)

    async def _refresh(self) -> None:
        conversations = await self.runtime.read.list_chat_conversations()
        for agent in conversations:
            if agent not in self._agents:
                self._agents.append(agent)
        self._sync_tabs()
        tabs = self.query_one("#chat_tabs", Tabs)
        counts: dict[str, int] = {}
        for agent in self._agents:
            counts[agent] = len(await self.runtime.read.list_chat_messages(agent))
            tab = tabs.query_one(f"#chat-{agent}", Tab)
            tab.label = self._tab_label(agent, counts[agent])
        self._seen[self.agent_name] = counts.get(self.agent_name, 0)
        pending = self.runtime.chat.pending(self.agent_name) if self.runtime.chat else False
        render_key = (self.agent_name, counts.get(self.agent_name, 0), pending,
                      len(self._errors.get(self.agent_name, [])))
        if render_key == self._last_render_key:
            return
        self._last_render_key = render_key
        log = self.query_one("#chat_log", RichLog)
        log.clear()
        for m in await self.runtime.read.list_chat_messages(self.agent_name):
            who = "you" if m.role == "user" else self.agent_name
            log.write(f"{who}: {m.text}")
        for err in self._errors.get(self.agent_name, []):
            log.write(err)
        if pending:
            log.write(f"… {self.agent_name} is thinking")

    # -- events ------------------------------------------------------------

    async def on_input_submitted(self, event) -> None:
        if event.input.id != "chat_input":
            return
        text = event.value.strip()
        event.input.value = ""
        if text:
            await self.app.send_chat_message(self.agent_name, text)
            self._last_render_key = ()

    def on_tabs_tab_activated(self, event) -> None:
        tab_id = event.tab.id or ""
        if tab_id.startswith("chat-"):
            agent = tab_id.removeprefix("chat-")
            if agent != self.agent_name:
                self.agent_name = agent
                self._last_render_key = ()
                self.query_one("#chat_input", Input).placeholder = f"message @{agent}…"

    def action_back(self) -> None:
        self.app.pop_screen()

    def _cycle(self, step: int) -> None:
        if len(self._agents) < 2:
            return
        idx = (self._agents.index(self.agent_name) + step) % len(self._agents)
        self.set_current(self._agents[idx])

    def action_prev_chat(self) -> None:
        self._cycle(-1)

    def action_next_chat(self) -> None:
        self._cycle(1)
```

Append to `novelizer/tui/app.tcss`:

```css
ChatScreen #chat_log {
    height: 1fr;
    border: solid $primary;
    padding: 0 1;
}
ChatScreen #chat_input {
    dock: bottom;
}
```

The test monkeypatches `app.send_chat_message`; the real method arrives in Task 6. To keep this task green standalone, add a minimal placeholder to `NovelizerApp` now (Task 6 replaces its body):

```python
    async def send_chat_message(self, agent_name: str, text: str) -> None:
        """Send a chat message and schedule reply generation (completed in the
        chat-routing change; ChatScreen calls this)."""
        message_id = await self.runtime.chat.send(agent_name, text)
        self.run_worker(self._chat_reply_worker(agent_name, message_id), exclusive=False)

    async def _chat_reply_worker(self, agent_name: str, replying_to: str) -> None:
        try:
            await self.runtime.chat.generate_reply(agent_name, replying_to)
        except Exception as e:
            line = f"⚠ {agent_name} reply failed: {e}"
            try:
                self.query_one("#feed", RichLog).write(line)
            except Exception:
                pass
            self.messages.append(line)
            from novelizer.tui.chat_screen import ChatScreen
            if isinstance(self.screen, ChatScreen):
                self.screen.add_error(agent_name, line)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/tui/test_chat_screen.py -q` → PASS
Run: `uv run pytest tests/tui -q` → PASS

- [ ] **Step 5: Commit**

```bash
git add novelizer/tui/chat_screen.py novelizer/tui/app.tcss novelizer/tui/app.py tests/tui/test_chat_screen.py
git commit -m "feat: ChatScreen — full-screen chat with tab strip, pending indicator, unread dots, async reply worker"
```

---

### Task 6: `@name` command routing and feed integration

**Files:**
- Modify: `novelizer/tui/app.py`
- Test: `tests/tui/test_chat_routing.py`

**Interfaces:**
- Consumes: `resolve_agent_name`, `CHAT_PERSONAS` (Task 3); `ChatScreen` (Task 5); `runtime.chat` (Task 4).
- Produces: `@<name> [text]` handling in `_run_command`; `format_event` renders `chat.agent_replied` previews; `_feed_loop` skips `chat.user_messaged`.

- [ ] **Step 1: Write the failing test**

Create `tests/tui/test_chat_routing.py`:

```python
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
    assert rendered.startswith("💬 Author replied:")
    assert len(rendered) < 120
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/tui/test_chat_routing.py -q`
Expected: FAIL — `@author …` falls through to `commands.dispatch` → "Unknown command", no ChatScreen; `format_event` renders the generic `◆ System` line.

- [ ] **Step 3: Implement routing + feed changes in `novelizer/tui/app.py`**

Add imports:

```python
from novelizer.chat.personas import CHAT_PERSONAS, resolve_agent_name
from novelizer.tui.chat_screen import ChatScreen
```

In `format_event`, after the `AGENT_REMARKED` branch:

```python
    if ev.event_type == EventType.CHAT_AGENT_REPLIED:
        label = _agent_label(p.get("agent_name", "?"))
        text = p.get("text", "")
        preview = text[:80] + ("…" if len(text) > 80 else "")
        return f'💬 {label} replied: "{preview}"'
```

In `_feed_loop`, skip the Director's own chat messages (before rendering):

```python
                for ev in events:
                    self._last_seq = ev.sequence
                    if ev.event_type == EventType.CHAT_USER_MESSAGED:
                        continue
                    rendered = format_event(ev)
                    log.write(rendered)
                    self.messages.append(rendered)
```

(Note: `self._last_seq` moves to the top of the loop body so skipped events still advance the cursor.)

At the top of `_run_command`:

```python
        stripped = line.strip()
        if stripped.startswith("@"):
            token, _, text = stripped[1:].partition(" ")
            agent = resolve_agent_name(token)
            if agent is None:
                known = ", ".join(f"@{n}" for n in CHAT_PERSONAS)
                msg = f"» unknown agent @{token} — try: {known}"
                self.query_one("#feed", RichLog).write(msg)
                self.messages.append(msg)
                return
            await self._open_chat(agent, text.strip())
            return
```

Add `_open_chat`:

```python
    async def _open_chat(self, agent_name: str, text: str) -> None:
        if isinstance(self.screen, ChatScreen):
            self.screen.set_current(agent_name)
        else:
            await self.push_screen(ChatScreen(self.runtime, agent_name))
        if text:
            await self.send_chat_message(agent_name, text)
```

(`send_chat_message` and `_chat_reply_worker` already exist from Task 5.)

Update the status bar hint in `_status_line` to advertise the feature — replace the trailing `":settings"` with `":settings · @agent <msg>"`.

- [ ] **Step 4: Run tests to verify pass**

Run: `uv run pytest tests/tui/test_chat_routing.py -q` → PASS
Run: `uv run pytest -q` → full suite PASS (existing feed/format tests must still pass — if a `format_event` test asserts on unknown events, the new branch must not change non-chat rendering)

- [ ] **Step 5: Commit**

```bash
git add novelizer/tui/app.py tests/tui/test_chat_routing.py
git commit -m "feat: @agent command routing to ChatScreen + chat reply previews in the feed"
```

---

## Final verification (after all tasks)

- `uv run pytest -q` — entire suite green.
- Manual smoke (optional, needs a running LLM endpoint): `uv run novelizer` (or the repo's usual TUI entry point), type `@author what should the next chapter do?`, watch the reply land; `Esc`; confirm the feed preview appeared.

## Self-review notes (already applied)

- Spec coverage: events/projection/read model (T2), never-gated (T2), shared intent helpers (T1), personas/permissions/aliases (T3), ChatService/runners/Runtime/source=chat/proposal gating (T4), ChatScreen/tabs/pending/unread/Esc (T5), routing/feed/errors (T6). Property tests: T2 (projection) — the T1 refactor guard is the delegated existing suite plus direct unit tests, per the spec's intent.
- The event-store-history deviation from the spec is documented in Global Constraints.
- Type consistency: `ChatMessageRecord` fields, `ChatReply.reply_text`, `runner_for`/`personality_for` callables, and `chat_<name>` injection keys are used identically across tasks.
