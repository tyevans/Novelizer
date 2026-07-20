# Fleet-shared prompt-surface redesign

Cross-cutting proposal covering every prompt surface that is NOT a single agent's `X_SYSTEM_PROMPT`. Scope items 1–7 from the brief. Read-only study of `/home/ty/workspace/novelizer`; nothing edited. Every claim cites `file:line`.

Research grounding: tech digest §§1–6, behavior digest §§2,5,6,7. "[tech §N]" / "[beh §N]" point at those.

---

## 1. Diagnosis

### 1.1 The retrieval note lives in the wrong module and predates the pull-tool doctrine
`RETRIEVAL_NOTE_BASE` / `RETRIEVAL_NOTE` are defined in `novelizer/agents/author.py:19-34` but imported fleet-wide: `character_keeper.py:5`, `retconner.py:4`, `editor.py:4`, `structure_analyst.py:5`, `continuity_checker.py:398`, `world_architect.py:7`, and `chat/runners.py:2`. Author is a leaf agent, yet it is the de-facto home of the shared retrieval contract — every other agent reaches *through* Author to get it. This is an accidental hub: touching Author's imports risks the whole fleet, and the name `RETRIEVAL_NOTE` reads as Author-private when it is a fleet constant.

The text itself (`author.py:19-30`) is a two-sentence tool advertisement:
> "You have file tools over the story canon (ls, read_file, grep, glob) and semantic search (search_canon). The chapter list below is an index — read any chapter or canon file you need in full before writing. Cite ids exactly as shown in frontmatter or search results."

Against tech digest §3 this is missing four load-bearing behaviors: (a) the **index-then-read loop** as a named motion [tech §3]; (b) the anti-pattern **"do NOT write from the push summary alone"** [tech §3] — the single most-cited fix for stale-summary drift; (c) a **stopping rule** to curb turn-burning [tech §3]; (d) the **search_canon vs grep division of labor** [tech §4]. It also re-explains the filesystem tools that deepagents' FilesystemMiddleware already describes [tech §1] — wasted attention budget.

### 1.2 The pass instruction is a one-way "skip" switch, not a three-way decision
`PASS_PROMPT_INSTRUCTION` (`base.py:25-29`):
> "If nothing needs your attention, set no_action=true, leave every list empty, and give a one-line feed_note in character saying you're standing aside so the story can continue."

Three defects against the abstain research: (a) it frames a **binary** act/skip, where the calibration win comes from a **three-way** {act, no_action, need-more-evidence} [tech §6, beh §7]; (b) it gives **no concrete trigger** — "nothing needs your attention" is exactly the vibe-based ambiguity that drives *both* over-acting and over-abstaining [tech §6]; (c) it has **no "don't be lazy" counterweight** — verify-then-abstain overshoots into refusing real work without a paired "silence on a real event is also a failure" clause [tech §6, beh §7.1]. It also does not tell the agent that a correct `no_action` is a *success* — agents "feel compelled to edit even after realizing nothing's wrong" [tech §6]. Only three of six eligible agents even use it (`character_keeper.py:29`, `continuity_checker.py:24`, `world_architect.py:15`); Editor/Structure/Retconner emit every pass by construction, which is defensible but undocumented.

### 1.3 Muse surfaces: static name-ban cannot scale; binding vs optional is well-shaped
`AI_TELL_BAN_NOTE` (`muse/prompts.py:8-12`) hard-codes five names (Elias/Elara/Mara/Thorne/Voss) and a handful of stock figures. Two problems: (a) it is **static** — a fixed blocklist catches yesterday's convergent names but not the crutch words *this* Author is currently over-using; behavior digest §6.4 prescribes a **rotating recently-used ledger** so "voice flattening" becomes a tracked, correctable signal, not a frozen list. (b) It lives in `muse/prompts.py` but is an **Author-prose concern**, not a Muse/corpus concern — Muse is non-LLM (`muse.py:29`) and never reads it; it is imported only by `author.py:10`. `casting_pool_note` (binding, `prompts.py:19-25`) and `inspiration_note` (optional, `prompts.py:28-43`) are actually well-designed: clear binding/optional split, empty-string when no hand. Keep their shape; only sharpen wording.

### 1.4 Brain notes: chapter identifiers are raw UUIDs — the worst ACI offender
`Chapter.id` is a raw UUID (`store/models.py:181`, `_uuid()` at `:13-14`), whereas threads/secrets/themes/characters use human slugs. So:
- `chapter_map_note` (`brain/context.py:85-93`) pushes `- [<uuid>] '<title>' ...` — the agent must copy a 36-char opaque string to cite a chapter.
- `causal_flags_note` (`brain/context.py:96-108`) prints `chapter <uuid> -> chapter <uuid>` — two UUIDs per line, and the Author must echo them back into `CausalIntent.cause_chapter_id/effect_chapter_id` (`schemas.py:121-123`).

Tech digest §4 is explicit: "Use natural-language / 0-indexed identifiers, never raw UUIDs … measurably improve retrieval precision and cut hallucination." The chapter UUID is the direct cause of any dropped/mis-cited causal edge. `known_secrets_note` (`brain/context.py:65`) prints `- '<slug>' (<title>)` — the id is citable (slug) but **quoting the id ahead of the title** inverts the natural-language-first ordering [tech §4]. `stale_threads_note` (`brain/context.py:25`) does it right: `- <name> (id:<slug>)` — name first, id in parens.

### 1.5 Two disconnected sources of truth for agent identity
Autonomous personality comes from `voices/default.toml:37-43` `[agent_personalities]` (thick, evocative sentences), injected uniformly as `\n\nIn character: {personality}` via `_guarded_line` for **every** agent (`author.py:49`, `editor.py:71`, `character_keeper.py:78`, `continuity_checker.py:107`, `structure_analyst.py:53`, `retconner.py:51`, `world_architect.py:44`). Chat identity comes from a **separate** one-liner `role_prompt` in `chat/personas.py:24-47`, sharing no text with the autonomous personality. So each agent has two authored voices that can drift apart, and — critically — the **fact-checking agents get a full-weight persona injection** identical to the generators'. Behavior digest §5.1 [HIGH]: personas raise perceived expertise but **reduce accuracy** on knowledge/discrimination tasks; they should be loaded heavy on Author/Muse and **light on Continuity/Retconner/Keeper/World/Structure**. Current code applies them flat.

### 1.6 No output schema forces the tool loop — the constraint tax is undefended
Not one draft schema carries an evidence/citation field (`grep` for `evidence|citation` in `schemas.py` returns only doc-prose mentions of "cite"). `ChapterDraft` (`base.py:32-40`), `KeeperOutput` (`schemas.py:132-138`), `ContinuityOutput` (`schemas.py:165-168`), `EditorVerdict` (`schemas.py:154-162`), `RetconDraft` (`schemas.py:126-129`) all let the model emit its final structure with **zero proof it read anything**. Tech digest §5 names this the #1 threat: `response_format=pydantic` biases the model to emit structure early and skip the tool loop ("constraint tax"), and the structural fix is a **required citation field that cannot be filled without reading** [tech §5]. The system currently relies entirely on prompt prose to get agents to use their tools, with no schema-level forcing.

---

## 2. Proposed replacement texts (paste-ready)

### 2.0 Move the shared surfaces into a new `novelizer/agents/prompts.py`
Create one module that imports nothing from other agents, so no agent is a prompt hub. Move `RETRIEVAL_NOTE_BASE`/`RETRIEVAL_NOTE` out of `author.py` and `PASS_PROMPT_INSTRUCTION`/`DEFAULT_PASS_REMARK` out of `base.py` into it. `base.py` keeps the *machinery* constants that are behavior, not prompt text (`GRAPH_RECURSION_LIMIT:19`, `PASS_BACKOFF_MULTIPLIER:23`). Re-export from `author.py` and `base.py` for one release (`from novelizer.agents.prompts import RETRIEVAL_NOTE  # re-export`) so the seven import sites in §1.1/§1.2 and the pinning tests keep resolving; then migrate imports. This is a **shared-surface move** — flagged for the architect.

### 2.1 `RETRIEVAL_NOTE` family (new home: `prompts.py`)
Keep the split (base for keepers, +map sentence for index-mode agents) since `author.py:32-34` and `character_keeper.py`/`retconner.py`/`editor.py`/`structure_analyst.py`/`world_architect.py` all rely on it and `test_author.py:668-691` pins both strings. Replace the body:

```python
_RETRIEVAL_NOTE_PREFIX = (
    "\n\n## Canon access\n"
    "You have file tools over the story canon (ls, read_file, grep, glob) and semantic "
    "search (search_canon). Work index-then-read: use grep/glob for an exact name, slug, "
    "or phrase and search_canon for a theme or 'where did X happen' when you don't know "
    "the words — LOCATE the file, then read_file only the span you need. "
)
_RETRIEVAL_NOTE_MAP_SENTENCE = (
    "The context below is an INDEX, not the source of truth: do NOT write or flag from a "
    "pushed summary alone — read the chapter or canon file in full before you commit any "
    "claim about it. "
)
_RETRIEVAL_NOTE_SUFFIX = (
    "Ground every id you emit in a file you actually read, and cite ids exactly as shown "
    "in frontmatter or search results. Once you can point to the line that supports your "
    "finding, stop searching and emit — don't browse past the evidence."
)
```

This adds all four missing behaviors from §1.1 (index-then-read loop, no-write-from-summary, grep-vs-search split, stopping rule) without re-teaching the tool mechanics deepagents already documents [tech §1,3,4]. `RETRIEVAL_NOTE_BASE` (keepers, no pushed chapter index) omits the middle sentence exactly as today. **Flags `test_author.py:668-691`** — those pinned strings must be updated to match (test-hook §6).

### 2.2 `PASS_PROMPT_INSTRUCTION` (new home: `prompts.py`)
```python
PASS_PROMPT_INSTRUCTION = (
    "\n\n## When to act vs stand aside\n"
    "First list the concrete changes in your lane since your last pass — a new or revised "
    "chapter, a new intent, a changed sheet. Then decide:\n"
    "- A real change you can act on with evidence -> act on it. Staying silent on a genuine "
    "development is a failure, not caution.\n"
    "- No change in your lane, or nothing you can ground in the canon -> set no_action=true, "
    "leave every list empty, and give a one-line feed_note in character saying you're "
    "standing aside. A correct stand-aside is a SUCCESS; inventing a marginal item to look "
    "busy is a failure.\n"
    "- A possible issue you can't yet confirm -> read the canon to confirm before you emit; "
    "do not emit on suspicion alone."
)
```
This installs the three-way decision, the concrete-delta anchor, the reward-correct-silence line, and the don't-be-lazy counterweight [tech §6, beh §7]. The default-abstain bias [beh §7.1] is carried by ordering "stand aside" as the fallback and requiring grounding to act. **Editor/Structure/Retconner intentionally omit this** (they act every pass) — document that in each agent prompt, not here.

### 2.3 Muse surfaces (`muse/prompts.py`)
**Move `AI_TELL_BAN_NOTE` out of `muse/prompts.py`** — it is an Author-prose rule, not a corpus concern; its only importer is `author.py:10`. Relocate to `prompts.py` (or an Author-local constant) and split into a stable ban + a dynamic ledger:

```python
# stable cluster ban (structure/punctuation tells) — beh §6.1-6.3
AI_TELL_BAN_NOTE = (
    "Avoid AI-prose tells: no more than one em-dash per paragraph; never use section "
    "headers or bullet lists inside chapter prose; ban filler ('it is worth noting', "
    "'significantly', 'crucially', 'a testament to', 'leverage', 'plethora'). Vary "
    "sentence length deliberately — smooth, evenly-cadenced prose reads as machine-made. "
    "Never name a character Elias, Elara, Mara, Thorne, or Voss, or lean on stock figures "
    "(lighthouse keepers, clockmakers, quaint coastal villages); avoid near-variants."
)

# dynamic, per-pass — beh §6.4. Empty string when the ledger is empty.
def recently_used_note(crutch_words: list[str]) -> str:
    if not crutch_words:
        return ""
    return (
        "\n\nWords/phrases you have leaned on in recent chapters — find fresh choices this "
        "time: " + ", ".join(crutch_words[:12])
    )
```
`crutch_words` is a small ledger the Structure Analyst (or a cheap n-gram counter over recent prose) maintains — turns voice-flattening into a tracked signal [beh §6.4]. **Keep** `casting_pool_note` and `inspiration_note` as-is in shape (`prompts.py:19-43`); only tighten `inspiration_note`'s lead to "optional — weave in any that genuinely fit, ignore the rest" (already close). `test_prompts.py:38-40` pins "Elias/Elara/Mara/Thorne/lighthouse" — all retained above, so that test still passes; the em-dash/filler additions are new assertions to add.

### 2.4 Brain notes (`brain/context.py`) — kill raw chapter UUIDs
The root fix is an **ordinal handle**. `build_path_index` already assigns each chapter an ordinal filename (`/chapters/001-the-salt-road.md`, per `canon_fs/search.py:35-44`). Surface that ordinal as the natural-language identifier, and keep the real id available only where the schema still requires it.

**`chapter_map_note`** (`context.py:85-93`) — lead with ordinal + title, drop the bare UUID:
```python
def chapter_map_note(chapters: list[Chapter]) -> str:
    if not chapters:
        return "None yet."
    return "\n".join(
        f"- ch{i:03d} '{c.title}' ({c.editorial_status.value}) "
        f"cast: {', '.join(c.character_ids) if c.character_ids else 'none'} [id:{c.id}]"
        for i, c in enumerate(chapters, 1)
    )
```
Ordinal `ch001` is what the agent reasons with; `[id:…]` trails for the one place a UUID is still required. **Flags `test_context.py:120-130`** (pins the exact `- [c1] '...'` line) — update to the new format.

**`causal_flags_note`** (`context.py:96-108`) — map chapter ids to ordinals before printing, so no UUID appears in the arrow line:
```python
def causal_flags_note(edges, chapter_order):
    candidates = find_paradoxes(edges, chapter_order)
    if not candidates:
        return ""
    ordinal = {cid: f"ch{i:03d}" for i, cid in enumerate(chapter_order, 1)}
    lines = "\n".join(
        f"- {ordinal.get(p.cause_chapter_id, p.cause_chapter_id)} -> "
        f"{ordinal.get(p.effect_chapter_id, p.effect_chapter_id)} ({p.reason})"
        for p in candidates
    )
    return f"\n\nCausal flags:\n{lines}"
```
**Flags `test_context.py:88-91`** (asserts `"c2" in note and "c1" in note`) — with the fixture `chapter_order=["c1","c2"]` those map to `ch001/ch002`; assertions must change to the ordinals or the mapping. The deeper follow-up (let `CausalIntent` accept ordinals and resolve them at commit) removes UUIDs from agent output entirely — flagged for the architect as a schema change beyond this pass.

**`known_secrets_note`** (`context.py:65`) — put the title first, id in parens, matching `stale_threads_note`'s good shape:
```python
lines.append(f"- {secret.title} (id:{secret.id}) — {who}")
```
**Flags `test_context.py:70`** asserts `"the-heir-lives" in note` (still true) and the format is otherwise unpinned on ordering, so this is low-risk.

**No change** to `stale_threads_note` (`:25`), `pacing_flags_note` (`:36`), `open_retcons_note` (`:81`) — already name-first / slug-cited / natural-language. `open_retcons_note` caps at 20 (`:81`) which is a good token guard [tech §4].

### 2.5 Chat personas (`chat/personas.py`) — one source of truth for identity
Make the autonomous `[agent_personalities]` (`voices/default.toml:37-43`) the **single source of truth** for who each agent is, and derive the chat `role_prompt` from it rather than authoring a second, thinner voice. Concretely: keep `ChatPersona` for the *permission* fields (`allow_threads`, `knowledge_actions`, etc. — those are real chat-specific policy, `personas.py:16-20`) but stop hand-writing `role_prompt`; instead compose it at runner-build time from a short fixed role line + the shared personality:
```python
# personas.py keeps only the role NOUN + permissions; personality comes from the voice pack
ROLE_LINES = {
    "author": "the Author — you write the chapters, thinking in scenes, beats, and consequences",
    "editor": "the Editor — you review chapters for quality, pacing, and voice",
    ...
}
# chat/runners.py:
persona_line = voice_pack.agent_personalities.get(agent_name, "")
role_prompt = f"You are {ROLE_LINES[agent_name]}." + (f" {persona_line}" if include_persona else "")
```
Then apply behavior digest §5.1 in **both** chat and autonomous paths: `include_persona=True` for author/editor (voice matters), lighter or omitted for the accuracy-first agents. This collapses the two identity sources into one and lets the persona-weight policy live in one place. `test_personas.py:11-13` asserts every persona has a non-empty `role_prompt` — satisfied by the composed line; `test_intent_permissions_mirror_autonomous_behavior` (`test_personas.py:16-30`) only touches permission fields, untouched.

### 2.6 `search_canon` docstring (`canon_fs/search.py:14-19`)
Rewrite as onboarding docs with a tool-boundary and an example [tech §4]:
```python
"""Semantic search over the whole story canon by MEANING — chapters, characters,
world entries, threads, secrets, themes. Use this when you don't know the exact
words: "where was the locket last seen", "scenes about betrayal". For an exact
name, slug, or quoted phrase, use grep instead — it is faster and exact.

Returns one line per hit: (kind) <canon file path> — '<title>' [id: <id>].
Read the file at that path for full content; cite the id exactly. Results are
ranked and capped; if you don't see what you need, narrow the query or add a
kinds filter, e.g. kinds=["secret"] to search only secrets.

Example: search_canon("the debt Mateo owes", kinds=["thread","secret"])
"""
```
`test_search.py:74-77` only asserts `tool.name == "search_canon"` and `"canon" in tool.description.lower()` — both hold. Also add explicit truncation signalling to the tool *response* when hits are capped ("… more results; narrow your query") [tech §4] — the current `search.py:40-44` returns all hits unbounded, a token risk on a large canon.

### 2.7 Schemas (`schemas.py`, `base.py`) — force the tool loop with citation fields
Add a lean `evidence` field on the **citation-bearing** intents (the ones that reference an existing id and therefore *must* have read it), not on every model — keeps the schema light per tech §5's "constraint tax rises with schema weight":
```python
class ThreadIntent(BaseModel):
    action: Literal["plant", "touch", "pay_off", "abandon"]
    name: str = ""
    id: str = ""
    note: str = ""
    evidence: str = ""   # file path or 'chNNN' the claim rests on; required for touch/pay_off/abandon
```
Same one-line `evidence: str = ""` on `KnowledgeIntent` (`schemas.py:89`) and `CausalIntent` (`schemas.py:110`). For the reactive judges add a pass-level `evidence: list[str]` on `ContinuityOutput` (`schemas.py:165`) and `EditorVerdict` (`schemas.py:154`) — a list of `file:line` citations backing the pass's flags. A `plant`/`introduce` action mints a new id and legitimately has no prior evidence, so keep `evidence` optional at the type level and enforce "required for citing actions" in the commit-time validator (`agents/intents.py`) with an actionable drop message, mirroring how unknown ids are already dropped. This is the structural constraint-tax defense [tech §5]: the schema now cannot be satisfied for a citing action without the agent having something to put in `evidence`, which forces the read. **New behavior — no existing test pins these fields**; add tests asserting a citing intent with empty `evidence` is dropped/logged.

---

## 3. Context-assembly conventions (fleet-wide)

- **Push a lean stable map, pull prose just-in-time.** The `_summarize()` pattern (`author.py:37-63`) inlines world (10×150ch, `:45`), characters (8, `:46`), and — outside pull_mode — 3 prior chapters at 200ch each (`:58`). That 200-char prose slice is exactly the "write from the summary" trap [tech §3] and is the same class of bug as the historical `prose[:300]` Character-Keeper starvation (memory: character-discovery-fix). **Recommendation: make pull_mode the default for every LLM agent** and let the pushed block be the index only (`chapter_map_note`), with the retrieval note (§2.1) forbidding write-from-summary. This also maximizes prompt-cache hits [tech §1]: keep the system prompt byte-stable and push only the volatile index in the user message.
- **Freshness windowing** [beh §7.4]: the pushed index should mark which chapters are new since the agent's last pass (the watermark already exists — `base.py:100-116` `_fingerprint`). Surface "new since your last pass: ch012–ch013" so the agent weights recent deltas and the §2.2 pass decision has a concrete anchor.
- **Middle-of-book scrutiny** [beh §1.1,1.3]: Continuity/Structure should get *full* fidelity on the most-recent and near-future chapters and compressed summaries for the middle — but the middle is exactly where errors cluster, so the index must still list every middle chapter by ordinal so the agent can pull it. Don't drop middle chapters from the index to save tokens; drop their prose, keep their handle.
- **Natural-language identifiers everywhere** (§2.4): once `chapter_map_note`/`causal_flags_note` speak in `chNNN`, the whole pushed context is UUID-free except the trailing `[id:…]` the schema still needs. Target state: schemas accept `chNNN` and resolve at commit, retiring the trailing id.

---

## 4. Behavioral guardrails (fleet-wide)

- **Three-way abstain, default-to-silence, reward-correct-silence** — installed by §2.2; applies to keepers/checkers/world. Generators (Author) and always-on agents (Editor/Structure/Retconner) do NOT get the pass instruction; document why in each agent prompt so it isn't read as an omission [tech §6, beh §7.1].
- **Research-then-emit ordering against the constraint tax.** Every LLM agent prompt should state the phase order explicitly: "First use tools to read and cite the canon. Only after you have your evidence, produce the structured output." [tech §5]. Pair with the §2.1 stopping rule so the tool loop terminates. The `evidence` fields (§2.7) are the structural backstop when the prose instruction is ignored.
- **Cite-the-line for judges** [beh §3.1]: Editor/Continuity output must be quote + location + specific problem + fix; the `evidence: list[str]` field (§2.7) operationalizes "no quote → no flag."
- **Cap and rank judge output** [beh §3.2]: Editor should emit a bounded, severity-ranked issue set, not an exhaustive dump — over-flagging trains the Author to ignore it. This is per-agent wording (Editor's own prompt) but the schema could add a soft cap.
- **Lane non-goals** [beh §2.1]: every agent prompt should carry a "not your lane — that's Agent Y's job" line (Continuity *finds* contradictions, does not rewrite → Retconner; Keeper tracks, does not author prose; Muse generates, does not police continuity). Cross-cutting convention; individual text lives in each system prompt.
- **write_todos at pass start** [tech §1]: the deepagents planning middleware is "basically a no-op" context device — the cheapest reliability lever. Add one line to the retrieval note or each prompt: "At the start of your pass, write a short todo list of what you'll check." (I left it out of §2.1 to keep that note tight; recommend it as a one-liner in the RESEARCH-then-emit block per agent.)

---

## 5. Persona / voice architecture

- **One source of truth.** `voices/default.toml [agent_personalities]` (`:37-43`) is the canonical personality; chat `role_prompt` (`personas.py:24-47`) should be *derived* from it (§2.5), not independently authored. Eliminates drift between the two voices an agent currently has.
- **Persona weight by lane** [beh §5.1, HIGH]. Generators (Author, and Muse's flavor text) carry the full personality; accuracy-first agents (Continuity, Retconner, Keeper, World, Structure) carry a **light or no** persona, because a thick persona measurably degrades knowledge/discrimination accuracy. Today all seven get the identical flat `\n\nIn character: {personality}` injection — change the injection to be conditioned on lane, in ONE place (a `persona_weight` per agent, applied in both `_summarize` and `chat/runners.py`).
- **Persona in the feed note, neutral instruction for the work** [beh §5.2]. Structural rule for every agent: do the analysis under neutral instructions, then render the `feed_note` in character. The current shape half-does this (the personality is appended as a trailing line, `author.py:49`), but the prompt doesn't tell the agent to confine the voice to the feed note — add: "Do your analysis plainly; put your personality only in the one-line feed_note." This keeps persona from bleeding into structured-intent extraction, where it hurts precision.
- **Prose voice (casting note) is Author/Editor only** and correctly separate from personality (`author.py:48` vs `:49`, `voices/default.toml` `prose_profiles` vs `agent_personalities`). No change to that split; it's right.

---

## 6. Risks & test hooks

**Tests that pin text (must update in lockstep with the change):**
- `tests/agents/test_author.py:668-691` — pins `RETRIEVAL_NOTE` and `RETRIEVAL_NOTE_BASE` byte-for-byte. §2.1 rewrites both; update the pinned strings. This is the highest-friction change (the assertion reconstructs the exact prefix/suffix split).
- `tests/brain/test_context.py:120-130` — pins `chapter_map_note` as `- [c1] '...' (draft) cast: ...`; §2.4 changes it to `ch001 … [id:c1]`. Update.
- `tests/brain/test_context.py:88-91` — `causal_flags_note` asserts raw `"c1"/"c2"` substrings; §2.4 maps to `ch001/ch002`. Update assertions.
- `tests/brain/test_context.py:70` — `known_secrets_note` asserts `"the-heir-lives" in note`; still true after §2.4 (id retained, reordered). Low risk.
- `tests/muse/test_prompts.py:38-40` — `AI_TELL_BAN_NOTE` must still contain Elias/Elara/Mara/Thorne/lighthouse; §2.3 retains all five. Safe; add new assertions for the em-dash/filler clauses.
- `tests/chat/test_personas.py:11-30` — asserts non-empty `role_prompt` + permission mirroring; §2.5's composed role line keeps both true, but if `role_prompt` becomes a computed property the test's `persona.role_prompt` access must still resolve.
- `tests/chat/test_runners.py:36,67` — asserts `RETRIEVAL_NOTE` presence/absence by identity; after the §2.0 move, the import path changes (`from novelizer.agents.prompts import RETRIEVAL_NOTE`) — keep the re-export or update the test import.
- `tests/canon_fs/test_search.py:74-77` — only checks name + "canon" substring; §2.6 rewrite is safe.

**Regression risks:**
- **Import move (§2.0)** is the riskiest structural change: seven modules import `RETRIEVAL_NOTE*` from `author.py` and three import `PASS_PROMPT_INSTRUCTION` from `base.py`. Do it with re-exports first, migrate importers in a follow-up, then delete the re-export — never a big-bang rename (memory: engineering-principles, SOLID/small steps).
- **`evidence` fields (§2.7)** could *raise* the constraint tax if made `required` on the schema (heavier schema → worse reasoning [tech §5]); keep them optional-at-type-level and enforce at commit time, so the schema stays lean. Live-LLM tests (`test_leak_live_llm.py`, `test_prose_mining_live_llm.py`) are the ones that would catch a real tool-loop regression — but memory says NEVER run suites in the main checkout (DB-lock incident); run in a worktree.
- **Ordinal identifiers** assume `enumerate(chapters)` order matches `build_path_index`'s ordinal (`search.py:35`). Verify both derive ordinals from the same chapter ordering before shipping, or a `ch012` in the map won't match the `012-…md` path the agent then reads.
- **pull_mode-by-default (§3)** removes the 200-char prose push (`author.py:58`) that some tests may assume; grep `prior_chapter_chars`/`previous` assertions in `test_author.py` before flipping the default.
