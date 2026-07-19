# Agent Chat Tabs — Design

**Date:** 2026-07-18
**Status:** Approved (brainstorm complete)

## Summary

Add interactive, in-persona conversations with the seven Novelizer agents from
the TUI. Typing `@author what if the mine collapse was deliberate?` in the
command input opens a full-screen chat with the Author, who answers in
character with story context. Conversations are event-sourced, one per agent,
and can produce real intents (threads, secrets, causal edges, themes) that flow
through the existing autonomy/proposal pipeline.

## Decisions (locked during brainstorm)

1. **Chat powers: consultation + proposals.** Agents converse AND may emit
   intents from chat; intents route through the existing
   `GatingCommitter`/proposal pipeline. No direct control-surface powers
   (no signal injection or work-cycle triggering from chat).
2. **Persistence: event-sourced.** Every user message and agent reply is an
   event in the canon event store. Chats survive restart and replay
   deterministically.
3. **Identity: one conversation per agent.** `@author` always continues THE
   Author conversation. Max seven conversations. No archiving in this cut.
4. **Separate brains.** Autonomous poll/work cycles do NOT see chat history.
   Steering the agent's autonomous behavior remains `:seed`/`:focus`/proposal
   approval.
5. **Layout: full-screen chat screens** with a tab strip of open conversations
   inside the chat screen. `Esc` returns to mission control.
6. **Replies: async + notify.** Generation runs in a background worker; the
   reply lands whenever ready. Feed shows a preview line; unfocused
   conversations get an unread marker. No streaming, no blocking.
7. **Architecture: new `novelizer/chat/` bounded context.** Agent classes are
   untouched; chat borrows persona and context recipes, not agent instances.

## Domain

### Events

Two new `EventType` members:

| Event                | Payload                                              | Aggregate id |
| -------------------- | ---------------------------------------------------- | ------------ |
| `chat.user_messaged` | `message_id`, `agent_name`, `text`                   | `agent_name` |
| `chat.agent_replied` | `message_id`, `agent_name`, `text`, `replying_to`    | `agent_name` |

The conversation aggregate is keyed by agent name — one conversation per
agent, so the agent name IS the conversation id.

Replies are committed only when generation completes. There are no partial or
streaming events. If the app dies mid-generation, the log shows an unanswered
user message; the user sends again. No auto-regenerate.

A failed generation (timeout, refusal, schema mismatch) commits **nothing**:
the event log never records a failure as speech. Failure is surfaced as
ephemeral UI state (see Error handling).

### Projection & read model

The existing `Projector` gains handlers for the two chat events, projecting
into a `chat_messages` table: `agent_name`, `role` (`user`/`agent`), `text`,
`message_id`, ordered by event sequence.

`ReadStore` gains:

- `list_chat_messages(agent_name, limit)` — transcript slice, oldest→newest.
- `list_chat_conversations()` — agent names having ≥1 message (drives the tab
  strip).

### Out of the domain

Unread markers and focused-tab state are TUI session state, not events.

## ChatService

Constructed on `Runtime` alongside `proposals`. UI-agnostic.

- `send(agent_name, text)` — appends `chat.user_messaged`; returns
  immediately. The TUI schedules `generate_reply` in a worker.
- `generate_reply(agent_name)` — assembles the prompt, invokes the agent's
  chat runner, appends `chat.agent_replied`, then commits any intents the
  reply carried.

### Chat runners

One per agent, built lazily on first use via the existing `build_chat_model` +
structured-output pattern:

- Same endpoint/model settings as that agent's autonomous runner,
  `agent_temperature`, and the 4096 max-token cap.
- Honors the `runners`-dict injection on `Runtime` (keys:
  `chat_<agent_name>`) so tests drive chat with fakes.
- Structured output schema `ChatReply`: `reply_text: str` plus
  `thread_intents`, `knowledge_intents`, `causal_intents`, `theme_intents`
  (the existing intent models).

### Context assembly

The prompt for a reply is:

1. **Chat system prompt** — the agent's role description plus its voice-pack
   `personality`, framed as: you are consulting with the Director; converse in
   character; you may optionally declare intents.
2. **Story context block** — the same read-store slices the agent sees in its
   autonomous prompts, reusing each agent's existing `poll()` context recipe
   where practical (recent chapters, active threads/secrets/themes as
   appropriate to that agent's role).
3. **Chat history** — the last ~20 messages from the `chat_messages`
   projection, so a restarted app resumes conversations seamlessly.

### Proposal path

Reply intents are committed under the agent's own name with `source="chat"`
for provenance, through the same `GatingCommitter`. In supervised autonomy
they surface as proposals in the existing pane and are approved/rejected with
`:approve`/`:reject` exactly like autonomous ones.

Per-agent `allowed_actions` mirror autonomous permissions (e.g.
`character_keeper` remains learn-only in chat).

**Refactor:** the `_commit_thread_intents` / `_commit_theme_intents` /
`_commit_knowledge_intents` / `_commit_causal_intents` validation logic is
extracted from `BaseAgent` into shared helpers (free functions or a small
`IntentCommitter`) in `novelizer/agents/`, used by both `BaseAgent` and
`ChatService`. One source of truth for the drop/downgrade rules.

## TUI

### Command routing

In mission control, a command-input line starting with `@` routes to chat:

- `@author <text>` — open (or return to) the Author chat and send `<text>`.
- `@author` — open the chat screen without sending.
- Names resolve case-insensitively against canonical agent names plus the
  existing short labels (`@keeper` → `character_keeper`, `@architect` →
  `world_architect`, `@continuity` → `continuity_checker`, etc.).
- Unknown name → feed error listing valid names.
- All non-`@` lines fall through to `commands.dispatch` unchanged.

### ChatScreen

One Textual `Screen`, parameterized by agent name.

- **Tab strip** (Textual `Tabs`) across the top: every conversation from
  `list_chat_conversations()` plus the current one.
- **Transcript**: scrollable view refreshing from the read store on a ~0.5 s
  poll loop (same worker pattern as existing widgets), so replies arrive
  whether or not the screen is focused.
- **Input**: the screen has its own `Input`; submitting sends to the current
  conversation.
- **Keys**: `ctrl+pgup`/`ctrl+pgdn` cycle conversations; `Esc` pops back to
  mission control.
- **Pending indicator**: `Author is thinking…` rendered when the last message
  is the user's and a generation worker is in flight.
- **Unread**: a session-only dot on non-focused conversations whose reply
  landed; cleared on focus.

### Feed integration

`_feed_loop` already streams every event. `format_event` renders
`chat.agent_replied` as a short preview (`💬 Author replied: "…"`, truncated)
and renders `chat.user_messaged` not at all (echoing the user's own message to
the feed is noise).

### Async workers

Reply generation runs in app-level workers (`run_worker`, non-exclusive), one
in flight per agent — a second message to the same agent while one is
generating queues rather than racing.

## Error handling

- Runner failure → no event committed; feed and transcript show
  `⚠ <agent> reply failed: <err>` (transcript line is ephemeral).
  User resends.
- Malformed/unauthorized intents → dropped with logged warning by the shared
  helpers, exactly as autonomous commits today.
- Unknown `@name` → feed error listing valid names.

## Testing

Red/green TDD with property-based coverage (house rules):

- **Property — projection determinism:** any interleaving of `chat.*` events
  across agents projects to per-agent transcripts preserving order
  (Hypothesis over event sequences).
- **Property — refactor guard:** extracted intent-commit helpers behave
  identically to the pre-refactor `BaseAgent` rules (same drops/downgrades).
- **Unit:** `@`-routing parser (names, aliases, bare mention, unknown agent);
  `ChatService.send`/`generate_reply` with fake runners via the `runners`
  dict — reply committed, intents gated into proposals in supervised mode,
  `source="chat"` provenance recorded.
- **TUI (Textual pilot):** `@author hi` pushes ChatScreen and appends the
  message; `Esc` pops; unread dot appears when a reply lands on an unfocused
  conversation.

## Out of scope

- Streaming replies.
- Multiple concurrent conversations with the same agent; archiving.
- Chat history in autonomous prompts (separate brains).
- Auto-regenerating interrupted replies.
- A new `story_architect` persona — the addressable set is the existing
  roster.
