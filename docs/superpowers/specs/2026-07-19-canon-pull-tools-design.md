# Canon Pull Tools — Design

**Date:** 2026-07-19
**Status:** Approved by Director (chat session), pending spec review

## Problem

Every agent today is a pure push consumer: `poll()` runs a fixed set of
`ReadStore` queries and injects curated, truncated context into a single
structured-output LLM call (`create_deep_agent(model, system_prompt,
response_format)` — no `tools=` anywhere). Truncation limits are chosen at
design time and do not scale with the story: the Author drafts chapter 40
having seen 200 chars each of chapters 37–39 and nothing of 1–36; the
Continuity Checker's per-chapter view (300 chars) thins as the book grows.
Push-only cannot survive a long book — that is structural, not tunable.

Meanwhile deepagents 0.6.12 silently attaches default middleware to every
agent: `write_todos`, virtual-FS file tools (`ls`, `read_file`, `write_file`,
`edit_file`, `glob`, `grep`) over an empty never-seeded filesystem, `execute`
(always errors — no sandbox backend), and `task` (no subagents defined). Dead
schema weight on every call, plus a failure mode where a model wastes turns
writing to a throwaway FS.

## Decision (approved)

Hybrid pull architecture:

1. **Canon as a read-only filesystem** — implement deepagents'
   `BackendProtocol` over `ReadStore`, so the *already-present* built-in file
   tools become real: agents can `read_file`, `grep`, `glob`, and `ls` the
   entire story.
2. **One curated semantic tool** — `search_canon(query, kinds?)` over
   everything (chapters, world entries, characters, threads, secrets) via the
   existing embeddings store.
3. **Push goes on a diet immediately** (not a later milestone): chapter-prose
   excerpts in prompts are replaced by a map (`id | title | status | cast`);
   agents pull full text on demand. No new summary machinery in phase 1.
4. **Writes stay exactly as they are.** The event-sourced intent path with
   commit-time id validation is a strength. FS `write`/`edit` return a firm
   error directing the model to declare intents instead.

### Rollout order (approved)

- **Phase a:** Author + Continuity Checker (worst truncation pain).
- **Phase c:** chat personas (same builder shape; biggest direct payoff for
  the Director in consultations).
- **Phase b:** all remaining scheduled agents — immediately after a and c.
  By then backend, renderers, and search tool are closed to modification;
  rollout is per-agent settings flag flips plus prompt-map updates.

SOLID discipline throughout: backend depends only on the `ReadStore`
interface; rendering is separated from routing; per-agent wiring is
composition in runner builders; later phases extend without modifying
phase-a code.

## Components

### 1. `CanonBackend` (new module: `novelizer/canon_fs/backend.py`)

Implements `deepagents.backends.protocol.BackendProtocol`: `ls`/`als`,
`read`/`aread`, `grep`/`agrep`, `glob`/`aglob`. Async variants are the
implementation (delegating to `ReadStore`'s async API) — agents run via
`ainvoke`, so the tool layer uses them; sync variants raise
`NotImplementedError` with a message naming the async path. `write`/`awrite`/`edit`/`aedit` return an error result:
"Canon is read-only. To change the story record, declare intents in your
structured response." `upload_files`/`download_files` likewise refuse.

Virtual tree:

```
/chapters/NNN-slug.md      # frontmatter: id, status, character_ids; body: full prose
/characters/slug.md        # traits, motivations, backstory, arc, voice, relationships, knows (non-revealed known secrets)
/world/slug.md             # world entry body, domain
/threads/slug.md           # state, touch_count, last note, last chapter id
/secrets/slug.md           # title, revealed flag, who knows it
/themes/slug.md            # title, touch_count, last note, last chapter id
```

Paths are deterministic: `NNN` = 1-based chapter ordinal, slugs derived from
titles/names with id-suffix disambiguation on collision. Every file's
frontmatter carries the exact record id — file reads feed the
cite-ids-exactly discipline.

The backend is a thin path router. It holds no rendering logic.

### 2. Renderers (new module: `novelizer/canon_fs/render.py`)

One pure function per record type: `render_chapter(Chapter) -> str`,
`render_character(Character, matrix, secrets) -> str`, etc. Markdown with
YAML-ish frontmatter. Reused later by TUI if wanted. Property-testable
without a database.

### 3. `search_canon` tool (new module: `novelizer/canon_fs/search.py`)

LangChain tool: `search_canon(query: str, kinds: list[str] | None)` →
top-k typed hits `(kind, id, title, snippet, score)`. Embeds the query with
the same model the theme-similarity path uses; cosine over the embeddings
store. Empty index / no hits returns "no results" (an answer, not an error).

**Delivered hit format:** each line is
`(kind) /path — 'title' [id: X]` — the canon-fs path replaces the
snippet/score shown above. The path is more useful than a snippet or raw
distance score: it lets the agent go read the full record directly via the
canon filesystem, which is the intended follow-up action after a hit.

**Indexing:** incremental, projector-side — when events that create/revise
canon content commit, the affected record is (re)embedded with a
`(kind, id)` tag. Same pattern as theme similarity. A one-shot backfill
indexes existing stories on first run after upgrade.

### 4. Wiring (runner builders + settings)

`build_author_runner` / checker / chat builders gain a composition step:

```python
create_deep_agent(model=..., system_prompt=..., response_format=...,
                  backend=CanonBackend(read_store),
                  tools=[search_canon_tool], ...)
```

gated by per-agent settings flags (`author_tools_enabled`, etc.; defaults
follow the rollout phases). `execute` middleware is excluded everywhere.
`write_todos` stays only where it plausibly helps (Author); excluded
elsewhere to cut schema weight.

*(As delivered: `execute` exclusion is free — deepagents auto-filters it
for non-sandbox backends. `write_todos` scoping was NOT implemented:
`TodoListMiddleware` is hardcoded in deepagents' stack and exclusion needs
a `HarnessProfile` entry-point plugin, disproportionate to the savings.
The retrieval note ships in two forms: full (with the chapter-index
sentence) for map-carrying agents, `RETRIEVAL_NOTE_BASE` for phase-b
agents that keep their push prompts.)*

### 5. Prompt changes

- Chapter excerpts in `poll()`-built prompts → map lines:
  `- [id] 'Title' (status) cast: names`.
  *(As delivered in CPT-M4 the cast field carries `character_ids`, not
  names — ids feed the cite-ids discipline and names live one `read_file`
  away.)*
- System prompts gain a short retrieval instruction: the map is an index;
  read or search what you need before writing/judging.
- World/character/brain notes (staleness, who-knows-what, causal flags,
  pacing) stay as-is in phase 1.

### 6. Telemetry

Tool calls emit events through the existing telemetry bus/callback path
(new `TelemetryEventType` members for tool start/end with tool name + args
summary), so the Engine Room shows each agent's research trail live. This
is both the delight feature and the evidence base for whether models
actually pull (informs any later push-diet tightening).

## Error handling

- Missing path → descriptive error string to the model (with a hint to `ls`
  the parent directory).
- Write/edit attempts → the read-only error above.
- Search with empty index → "no results".
- Loop runaway bounded by deepagents' recursion limit + existing
  `llm_max_tokens` cap.

## Testing

- Renderers and path routing: unit + property tests (pure functions, no DB).
- `CanonBackend` over a seeded in-memory `ReadStore`: integration tests.
- Tool-loop behavior: fake-runner harness like existing agent tests.
- All test runs in a worktree, never the main checkout (standing rule).

## Out of scope (explicitly)

- New summary-generation machinery for the chapter map.
- Any change to the write path / intent validation.
- Subagents (`task` middleware) — future consideration.
- Shrinking world/character/brain-note push blocks.
