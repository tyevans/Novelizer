# Novelizer agent-prompting architecture brief (2026-07-19)

Shared context for per-agent prompt-redesign subagents. Repo: /home/ty/workspace/novelizer (read-only for you — do NOT edit files; your deliverable is a written proposal).

## System shape

Novelizer is an event-sourced, DDD-structured multi-agent system that writes a novel continuously. Agents run on intervals under a director/scheduler; each cycle is poll → work (one LLM invocation of a deepagents graph) → commit (events to the canon store). The canon is also rendered as a markdown filesystem ("canon_fs") that agents can browse with tools.

## Agents (novelizer/agents/)

| Agent | File | LLM? | Output schema | Notes |
|---|---|---|---|---|
| Author | author.py | yes | ChapterDraft (title, prose, character_ids, feed_note, thread/knowledge/causal/theme intents) | pull_mode swaps prose excerpts for chapter index; revise flow on editor signal |
| Editor | editor.py | yes | verdict approve/revise + notes | emits revise signals to Author |
| Continuity Checker | continuity_checker.py | yes | retcon_requests; separate MINING_SYSTEM_PROMPT pass mines prose for uncovered facts (secrets/threads/causal/inspiration facts) | pull_mode |
| Character Keeper | character_keeper.py | yes | new_characters, updated_characters, retcon_requests | had prose[:300] truncation bug historically (fixed) |
| Retconner | retconner.py | yes | amended_entries (supersedes_id) | consumes approved retcon requests |
| Structure Analyst | structure_analyst.py | yes | per-chapter tension score 0-1 + pacing_label | |
| World Architect | world_architect.py | yes | 1-3 world entries (title, body, domain, tags) | |
| Muse | muse.py | NO — pure corpus card dealer | commits InspirationDrawn hands (names, professions, settings, beats) | its prompt surfaces live in muse/prompts.py and are injected into Author/World Architect/Checker prompts |

## Prompt assembly pattern (uniform across LLM agents)

1. `X_SYSTEM_PROMPT` constant — terse role + task + output-field description (3-15 lines).
2. Some add `PASS_PROMPT_INSTRUCTION` (base.py): "If nothing needs your attention, set no_action=true... one-line feed_note in character saying you're standing aside". Used by: character_keeper, world_architect, continuity_checker (idle-pass mechanism; pass → 3x interval backoff).
3. All LLM runners append a retrieval note (author.py): RETRIEVAL_NOTE_BASE = "You have file tools over the story canon (ls, read_file, grep, glob) and semantic search (search_canon). Cite ids exactly as shown in frontmatter or search results." Author + Checker use the longer RETRIEVAL_NOTE adding "The chapter list below is an index — read any chapter or canon file you need in full before writing."
4. User message built per-cycle by `_summarize()`-style functions: inlined truncated context — world lore (10 entries × 150 chars), characters (8), previous chapters (3 × 200 chars prose) OR chapter_map index in pull_mode, director signals, plus conditional "brain notes" from brain/context.py (stale threads w/ ids, secrets who-knows-what matrix, causal paradox flags, pacing flags, open retcon dedup list) and muse notes (casting pool binding names, inspiration hand optional, AI_TELL_BAN_NOTE with banned names Elias/Elara/Mara/Thorne/Voss).
5. `personality` (from voices layer/settings) appended as "In character: {personality}"; Author also gets "Write in this prose voice: {casting_note}".
6. Runner: `deepagents.create_deep_agent(model, system_prompt, response_format=Schema, backend=canon_fs_backend, tools=[search_canon])`, recursion_limit=100. Structured response read from result["structured_response"].

## Chat surface (novelizer/chat/personas.py)

Separate one-line `role_prompt` per agent for interactive @-mention chat, with per-agent intent permissions (chat is not privilege escalation). These personas are much thinner than the autonomous prompts and share no text with them.

## Known history / constraints (from project memory)

- Event sourcing, DDD, SOLID, red/green + property-based TDD are non-negotiable house rules.
- CPT-M1..M6 (canon pull tools) merged 2026-07-19: every agent tooled.
- Character discovery bug: prose[:300] truncation starved Character Keeper — root cause of missed characters; the planned fix direction is a "context-assembly protocol" replacing char cutoffs (pull-mode is the vehicle).
- Idle-pass mechanism: watermark readiness + no_action pass just merged; pass backoff = 3 intervals.
- GRAPH_RECURSION_LIMIT raised to 100 because tool-heavy passes exceeded 25 and 50.
- AI_TELL_BAN_NOTE cites Hamilton & Mimno (Cornell 2026): banned convergent AI names/figures.

## Your deliverable format (each per-agent subagent)

Return a proposal with these sections:
1. **Diagnosis** — concrete weaknesses of the current prompt+context assembly for THIS agent (quote current text; cite file:line).
2. **Proposed system prompt** — full rewritten text, ready to paste, composed to work WITH deepagents' internal scaffolding and the shared retrieval note (or propose changes to the shared notes, flagged as shared-surface changes).
3. **Context-assembly changes** — what the user message should contain vs what the agent should pull via tools; specific _summarize() changes.
4. **Behavioral guardrails** — pass/no_action calibration, over/under-retrieval, structured-output pitfalls, lane boundaries vs other agents.
5. **Persona/voice** — how personality + feed_note + chat persona should relate for this agent.
6. **Risks & test hooks** — what could regress; which existing tests constrain wording (grep tests/ for prompt-text assertions before proposing renames).
