# Character Keeper — prompt & context redesign proposal

Agent: `novelizer/agents/character_keeper.py`. Schema: `KeeperOutput` (`novelizer/agents/schemas.py:132`). Class: canon-keeper (behavior digest §Cross-cutting: *minimal persona, structured-intent output, explicit non-goals*).

---

## 1. Diagnosis

**D1 — The truncation bug was widened, not removed. Same bug class, bigger number.**
`work()` builds chapter context as `c.prose[:self._prose_chars]` (`character_keeper.py:77`), with `prose_chars=6000` (`character_keeper.py:41`). The historical `prose[:300]` starvation (memory: character-discovery-fix, merged f398dab) is now `prose[:6000]` — a character introduced past char 6000 of a chapter is *still* invisible, exactly as one past char 300 was. The truncation is asserted as intended behavior by `test_work_prompt_caps_prose_at_configured_prose_chars` (`tests/agents/test_character_keeper.py:250`). Tech digest §3 names the fix: *"Do NOT write from the push summary alone… require the agent to read the relevant canon before emitting."* The Keeper does the opposite — it is *handed* a truncated push and never told to read the source.

**D2 — Tools are attached but never used for the one job that needs them.**
The tooled builder appends `RETRIEVAL_NOTE_BASE` (`character_keeper.py:176`), which — unlike the Author's `RETRIEVAL_NOTE` — deliberately omits the "read any chapter in full" sentence (`author.py:32` vs `:34`; asserted by `test_build_character_keeper_runner_with_backend_uses_retrieval_note_base`, `test_character_keeper.py:441`). So the agent is told file tools *exist* but is never told the pushed prose is truncated or to pull the full chapter. Worse, `work()` (`character_keeper.py:73–81`) has **no `pull_mode`** at all — it inlines truncated prose in every mode, unlike its siblings `Author` (`author.py:55`) and `ContinuityChecker` (`continuity_checker.py:109`) which swap prose for `chapter_map_note` when tooled. The Keeper is the fleet outlier that still hard-pushes prose.

**D3 — `knowledge_intents` (learn) is a live capability the prompt never mentions and the context never feeds.**
`commit()` processes `out.knowledge_intents` with `allowed_actions={"learn"}` (`character_keeper.py:144–147`); the schema and chat persona both grant it (`schemas.py:132`, `personas.py:37`); a test asserts it commits `secret.learned` (`test_character_keeper.py:115`). Yet the `SYSTEM_PROMPT` (`character_keeper.py:16–29`) lists only 4 tasks and never mentions secrets. And `poll()` fetches `secrets` (`character_keeper.py:68`) but `work()` never renders them into the message (`character_keeper.py:76–80`) — so the model has **no secret ids to cite** and cannot know the current knowledge state. The capability is structurally dead: undocumented and unfeedable.

**D4 — The load-bearing dedup instruction is starved of the data it needs.**
The prompt's strongest rule — *"Never re-report a character already in the cast, even under a nickname or variant spelling"* (`character_keeper.py:20–21`) — has no supporting context. The cast list shown to the model is `name (id:…): traits, arc` only (`character_keeper.py:76`); it omits `aliases`, which the `Character` model carries (`store/models.py:79`) and `render_character` surfaces in frontmatter (`render.py:39`). And the output schemas `NewCharacter`/`CharacterUpdate` (`schemas.py:31–52`) have **no `aliases` field**, so a discovered nickname can never be recorded back. The agent is told to dedup against nicknames it cannot see and cannot write down.

**D5 — Four (really five) tasks, flat, with no research-then-emit ordering → maximal constraint-tax exposure.**
The prompt is a flat list of tasks (discover, arc-update, contradiction-flag, voice) plus the hidden knowledge task, with `response_format=KeeperOutput` (`character_keeper.py:179`). Tech digest §5 flags this as *the #1 threat*: structured output biases the model to emit the final structure early and skip the tool loop ("constraint tax"). With truncated prose already in the prompt (D1/D2) and no instruction to research first, the model will finalize from the push — reproducing the discovery bug even with tools attached. There is no `write_todos` plan step, no stop rule, no citation field forcing a read.

**D6 — `no_action` is generic, not verify-then-abstain anchored to a delta.**
The only abstain guidance is the shared `PASS_PROMPT_INSTRUCTION` (`base.py:25–29`): "If nothing needs your attention…". Behavior digest §7.2 and tech digest §6 both call for a *concrete delta anchor* ("what new characters/changed behavior appeared since my last pass?"). Watermarking gates dispatch (`character_keeper.py:58–61`), but the prompt itself gives the model no checkable trigger, so on a re-dispatch it will pattern-match "there are characters and chapters → do something."

**D7 — Retcon lane overlaps the Continuity Checker with no boundary.**
Both agents emit `retcon_requests` (`KeeperOutput` vs `ContinuityOutput`, `schemas.py:132/165`) and both are handed the same open-retcon dedup note (`open_retcons_note`, `context.py:71`). The Keeper's Task 3 says "behavioral contradictions" (`character_keeper.py:24`) but never scopes *out* timeline/plot/factual continuity, which is the Checker's job (`continuity_checker.py:19`). Behavior digest §2.1 requires an explicit "not your lane" clause naming the sibling.

---

## 2. Proposed system prompt

Two coordinated pieces: (a) a rewritten static `SYSTEM_PROMPT` (tool-agnostic core, cache-stable), and (b) a Keeper-specific retrieval/pull note appended only in tooled mode (replacing the bare `RETRIEVAL_NOTE_BASE` append). Both are paste-ready.

### (a) `SYSTEM_PROMPT` — replace `character_keeper.py:16–29`

```python
SYSTEM_PROMPT = """You are the Character Keeper for a living fictional world. You maintain the \
canonical cast: you discover characters the prose introduces, keep each character's sheet true to \
what recent chapters show, and record when a character learns a secret. You work from what the prose \
actually says, never from what you expect.

## Your lane
- Discover new_characters: named people who appear in the prose but are missing from the cast. Give \
each the name spelled exactly as the prose spells it, plus traits, motivations, backstory, arc_status, \
and voice that the prose itself shows — leave a field blank rather than invent it.
- Update existing characters: revise arc_status, and correct traits/motivations/backstory/voice, to \
match what recent chapters show. Note voice as concrete dialogue patterns, vocabulary, and verbal tics \
you can quote, and refine it as a character's voice evolves.
- Record knowledge: when a chapter shows a character learning a secret on the page, emit a knowledge \
intent (action="learn", the secret's id, the character's id). Report the moment of learning only — a \
character merely acting on a secret is not yours to record.
- Flag character contradictions: when a character's canonical trait and their prose action genuinely \
conflict, file a retcon_request (what conflicts with what, the conflicting ids, a proposed resolution).

## Not your lane
- You do not rewrite prose or invent characters, arcs, or events the prose does not show. That is the \
Author's work.
- You do not chase timeline, factual, or world-logic contradictions — dates, locations, quantities, \
anachronisms. That is the Continuity Checker's lane; your retcons are strictly about a named \
character behaving against their established sheet.
- You do not resolve retcons or amend canon entries — you file, the Retconner repairs.
- You do not plant, reveal, or invent secrets — only record a character learning an existing one.

## De-duplication is the job
Before reporting a new character, prove they are new. Check the cast list AND each character's aliases \
for the same person under a nickname, a title, a first-name-only reference, or a variant spelling \
(e.g. "Doc" for "Dr. Reyes", "the sergeant" for a named soldier). If the prose reveals a new name for \
an existing character, do NOT create a duplicate — add it to that character's aliases via an update. \
Re-reporting an existing character under a new label is the failure mode to avoid.

## How to decide when to speak
Speak only on a real change since your last pass. First list, to yourself, the concrete deltas: which \
named characters are new to the prose, whose behavior or voice changed, who learned a secret. If that \
list is empty, that is a correct and successful pass — set no_action=true, leave every list empty, and \
give a one-line feed_note in character saying you are standing aside. Inventing a marginal update to \
look busy is a failure. But a genuine new character or a real behavioral shift you stay silent on is \
equally a failure: when the prose has moved, you must act.

## Output
Return new_characters, updated_characters, retcon_requests, and knowledge_intents (learn only). \
You may be shown retcon requests already filed and still open — do not re-report those, even reworded."""
```

Note: `PASS_PROMPT_INSTRUCTION` is now **folded into** the "How to decide when to speak" section (verify-then-abstain, delta-anchored), so it is no longer concatenated — see §6 for the test this touches. If you prefer to keep the shared append for consistency, drop the last two sentences of that section and keep `+ PASS_PROMPT_INSTRUCTION`; the delta-anchoring is the part worth keeping bespoke.

### (b) Keeper pull note — replace the `RETRIEVAL_NOTE_BASE` append at `character_keeper.py:176`

Define alongside the Author's notes (or in `character_keeper.py`) a Keeper-specific note that adds the index-then-read discipline the base note omits:

```python
KEEPER_PULL_NOTE = (
    "\n\nYou have file tools over the story canon (ls, read_file, grep, glob) and semantic search "
    "(search_canon). The characters and chapter index in the message below are a MAP, not the source: "
    "the chapter lines are titles and ids only. Before reporting anything, work in two phases.\n"
    "1. RESEARCH: read every chapter new since your last pass IN FULL with read_file — a character can "
    "be introduced in the last line of a chapter, so never judge a chapter from its title or an excerpt. "
    "Use grep to check whether a name you are about to report already appears as an existing character's "
    "name or alias. Read a character's file when you need their current sheet.\n"
    "2. EMIT: only once you have read the prose behind a finding, produce the structured output. Ground "
    "each new character and each contradiction in the chapter you read it in (cite the chapter title or "
    "id in the relevant note field). When your findings are grounded, stop searching and emit — do not "
    "keep browsing. Cite ids exactly as shown in frontmatter or search results."
)
```

And in the tooled branch (`character_keeper.py:176`):

```python
system_prompt = SYSTEM_PROMPT + KEEPER_PULL_NOTE
```

This composes WITH deepagents' base prompt (tech digest §1): it references `read_file`/`grep`/`glob`/`search_canon` by name rather than re-explaining them, names the RESEARCH→EMIT phase order (§5 constraint-tax mitigation), and gives an explicit stop rule (§3).

---

## 3. Context-assembly changes (`work()` / `poll()`)

The prompt in §2(b) only works if the message actually becomes a map. Mirror `Author`/`ContinuityChecker` by adding `pull_mode` to `CharacterKeeper`.

**Add `pull_mode: bool = False` to `__init__`** (mirror `continuity_checker.py:56,61`) and wire it in `runtime.py` from `s.character_keeper_tools_enabled` (mirror `runtime.py:177,202`; the tooled builder selection at `runtime.py:184` already keys on the same flag).

**Rewrite `work()` context block** (`character_keeper.py:76–80`):

```python
chars = "\n".join(
    f"- {c.name} (id:{c.id}): aliases={', '.join(c.aliases) or '—'}; traits={c.traits}; arc={c.arc_status}"
    for c in ctx["characters"]
) or "None yet."
retcons = open_retcons_note(ctx.get("open_retcons", []))
secrets = known_secrets_note(ctx["secrets"], ctx["characters"], ctx["knowledge_matrix"])  # NEW
if self.pull_mode:
    chapters_block = f"Chapter index:\n{chapter_map_note(ctx['recent'])}"
else:
    chapters_block = "Recent chapters:\n" + (
        "\n\n".join(f"Chapter '{c.title}': {c.prose[:self._prose_chars]}" for c in ctx["recent"]) or "None."
    )
cast = self._guarded_line("In character", self.personality)
msg = f"Characters:\n{chars}\n\n{chapters_block}{secrets}{retcons}{cast}"
```

Changes, each tied to a diagnosis:
- **Cast line now includes `aliases`** (fixes D4's read side). Requires `poll()` to already return `Character` objects — it does (`character_keeper.py:66`).
- **`known_secrets_note` added** (fixes D3): the same who-knows-what block the Author gets (`author.py:51`), so the model has real secret ids + current knowledge state to cite `learn` intents against. `poll()` already fetches `secrets` (`character_keeper.py:68`); add `"knowledge_matrix": await self._read.knowledge_matrix()` to `poll()`.
- **`pull_mode` swaps truncated prose for `chapter_map_note`** (fixes D1/D2): in tooled production the model gets an index and is required (by §2b) to `read_file` the new chapters in full. Bare mode keeps the current inline-prose fallback since no tools exist.

**What stays pushed vs pulled** (tech digest §3 hybrid model): push the lightweight *map* — cast (names/ids/aliases/traits/arc), secrets matrix, open-retcon dedup list, chapter index (titles/ids). Pull *prose* — full chapter text via `read_file`, character sheets when a sheet is needed. This is the exact push/pull split the Author and Checker already use.

---

## 4. Behavioral guardrails

- **Constraint tax (highest risk here):** the RESEARCH→EMIT ordering in §2(b) plus the chapter *index* (not prose) in §3 are the paired mitigation. Recommend also instructing a `write_todos` plan at pass start ("list new chapters → read each in full → grep names against cast → check learned-secret moments"); tech digest §1 calls this the cheapest reliability lever and it directly counteracts premature finalization. Optional but low-cost; add as a first line of `KEEPER_PULL_NOTE` if desired.
- **Over-retrieval:** the stop rule ("when findings are grounded, stop searching and emit") bounds turn-burning against `GRAPH_RECURSION_LIMIT=100` (`base.py:19`).
- **Verify-then-abstain / over-abstention:** §2(a) "How to decide when to speak" gives the delta anchor AND the balancing "silence on a real change is a failure" clause (behavior digest §7.1 vs the §7.1-backlash). Commit-side pass handling is already correct (`character_keeper.py:87–90`; property test `test_no_action_pass_never_mutates_canon`).
- **Lane boundaries:** the "Not your lane" block (§2a) names Author (prose/invention), Continuity Checker (timeline/factual retcons), Retconner (repair), and the secret-authoring boundary — the Keeper is `learn`-only, which the commit path enforces regardless (`character_keeper.py:146`), but the prompt should not tempt what the code will silently drop (`test_character_keeper_commit_drops_non_learn_actions`).
- **Structured-output pitfalls:** schema stays lean (§5 says minimize required fields) — I do **not** propose a new required `evidence` list, because grounding is instead pushed into existing free-text note fields (chapter id in the retcon description / a new-character context). If the team wants the stronger structural forcing from tech digest §5, add an optional `evidence: str = ""` to `NewCharacter`/`RetconDraft`; flagged as a schema change in §6.

---

## 5. Persona / voice

Canon-keeper → **minimal persona** (behavior digest §5.1: personas help voice, hurt accuracy; load heavy persona only on Author/Muse). Current handling is already correct and should be preserved:
- Analytical work runs under neutral instructions; `personality` is injected only as a trailing `In character: {personality}` line (`character_keeper.py:78` via `_guarded_line`), and the in-character voice surfaces only in the `feed_note` (`character_keeper.py:148`). This is exactly the "plain instruction for the task, persona for the feed note" split (§5.2). Keep it — do not thicken.
- Chat persona (`personas.py:35–38`) is a thin one-liner with `knowledge_actions={"learn"}` mirroring the autonomous permission. Correct and consistent; no change. It intentionally shares no text with `SYSTEM_PROMPT` (architecture brief §Chat surface) — keep that separation.

---

## 6. Risks & test hooks

**Tests whose wording/behavior my changes touch (grepped `tests/`):**

- `test_build_character_keeper_runner_with_backend_uses_retrieval_note_base` (`test_character_keeper.py:433`) — asserts the tooled prompt ends with `RETRIEVAL_NOTE_BASE` and that "chapter list below" is absent. Switching to `KEEPER_PULL_NOTE` **breaks this**; update it to assert the pull note is appended (the "read… IN FULL" discipline). Intended change, not a regression.
- `test_work_prompt_caps_prose_at_configured_prose_chars` (`test_character_keeper.py:250`) and `test_work_prompt_includes_characters_introduced_late_in_a_chapter` (`:236`) — both assert **bare-mode** truncation/inclusion behavior. They keep passing because bare mode retains inline prose. But add a **pull-mode** counterpart: assert that with `pull_mode=True` the message contains the chapter *title/index* and NOT the raw prose body — this is the test that would have caught the original bug class at the context layer.
- `test_work_prompt_includes_personality_when_set` (`:61`), `test_work_prompt_lists_open_retcons` (`:178`), `test_work_prompt_omits_retcon_block_when_queue_empty` (`:191`) — assert substrings still present in the new `msg` layout ("In character:", "already filed (do not re-report these)"). My `msg` keeps `cast` and `open_retcons_note` verbatim, so these hold. Verify ordering doesn't break the `omits` test (it checks absence, safe).
- `test_work_prompt_includes_characters_introduced_late_in_a_chapter` passes a >300-char, <6000-char prose in **bare** mode; unaffected.
- `known_secrets_note` addition: `test_character_keeper_uptake.py` and the `learn` tests (`:115`) use `FakeRunner` and don't inspect the message, so they're unaffected; but the new `poll()` key `knowledge_matrix` must be present or `known_secrets_note` will KeyError — add it to `poll()` and to any test that constructs `ctx` by hand (`test_keeper_pass_uses_default_remark_when_feed_note_empty` at `:380` passes a literal ctx dict `{"characters":…, "recent":…, "secrets":…, "hands":…}` to `commit()`, not `work()`, so it's safe; `work()` is only reached via `poll()` in tests).

**Regression risks:**

- **Cost/latency:** pull mode turns one prompt into a read-loop (N `read_file` calls per pass). Mitigated by the stop rule and by watermarking (`_fingerprint`, `character_keeper.py:58`) firing the agent only on real chapter deltas. Acceptable given `recursion_limit=100`.
- **Bare-mode discovery gap persists by design:** with no tools, prose[:6000] truncation remains. Ensure production runs with `character_keeper_tools_enabled=True` so pull mode is active — otherwise D1 is only half-fixed. Worth a one-line note in the runtime config.
- **Schema changes are the biggest blast radius.** Adding `aliases` to `NewCharacter`/`CharacterUpdate` (to fully fix D4's write side) touches `schemas.py`, the `commit()` mapping (`character_keeper.py:107–131`), `render_character`, and `test_schemas`. This is a real feature, not a prompt tweak — recommend it as a **fast-follow** so the alias *read* fix (cast line shows aliases) ships first and de-risks the dedup instruction immediately, with alias *write-back* as the second PR.
- **Prompt-caching:** keeping `SYSTEM_PROMPT` static and pushing all volatile state into the user message (§3) preserves the automatic cache on the static section (tech digest §1) — the redesign does not regress this.
