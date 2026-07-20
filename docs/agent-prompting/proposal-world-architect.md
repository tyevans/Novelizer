# World Architect — prompt & context redesign proposal

Scope: `novelizer/agents/world_architect.py` (SYSTEM_PROMPT, poll/work, runner), the shared
`RETRIEVAL_NOTE_BASE` it inherits, `architect_settings_note` (muse/prompts.py), and the
`WorldEntriesDraft`/`WorldEntryDraft` schema (schemas.py). All citations are `file:line`.

---

## 1. Diagnosis

**D1 — The Architect is blind to the story it exists to serve.** `poll()` fetches only world
entries, director signals, and the Muse hand (`world_architect.py:34-39`). It never reads a
single chapter. `work()` builds its user message from `ctx["entries"]` and `ctx["signals"]`
only (`world_architect.py:42-46`). So the prompt's core instruction — "identify thin or
unexplored areas and expand them" (`world_architect.py:13-14`) — has no story input to reason
from. The agent can only free-associate against its own list of existing entries. This is the
exact anti-pattern the brief names ("not free-associate") and the direct cause of
encyclopedia-padding: lore is generated for its own sake, disconnected from what recent
chapters actually touched. Compare Character Keeper, which polls `chapters[-5:]`
(`character_keeper.py:63-71`) and reads up to 6000 chars of each; the Architect's `body[:100]`
world summary (`world_architect.py:42`) is its *entire* world model.

**D2 — Pull tools are wired but the prompt never tells it to survey.** The runner attaches the
canon filesystem backend and `RETRIEVAL_NOTE_BASE` (`world_architect.py:79-83`), so `ls`,
`grep`, `glob`, `read_file`, and `search_canon` exist. But `RETRIEVAL_NOTE_BASE` only says the
tools *exist* and "Cite ids exactly as shown" (`author.py:19-32`); nothing instructs the
Architect to actually survey chapters or existing entries before emitting. Given
`response_format=WorldEntriesDraft` (`world_architect.py:81`), the constraint-tax failure mode
(tech digest §5: schema suppresses the tool loop, the model emits structure early) means the
Architect will, by default, skip retrieval entirely and generate from the truncated push
summary. No prompt force pulls it into the tools.

**D3 — No consistency-before-canonize duty.** The brief requires every new entry be checked
against existing entries in its domain before emitting. The prompt has no such instruction, and
the push context is capped at 20 entries × 100 chars (`world_architect.py:42`) — so if the
world already has 30+ entries, the Architect literally cannot see the ones it might duplicate or
contradict. `WorldEntryDraft` carries a `supersedes_id` field (`schemas.py:15`) but the
Architect is never told it exists or when to use it (that field is really the Retconner's lane;
see D6).

**D4 — `no_action` guidance is generic and has no concrete delta criterion.** The only abstain
instruction is the shared `PASS_PROMPT_INSTRUCTION` (`base.py:25-29`), which says "If nothing
needs your attention, set no_action=true." There is no verify-then-abstain trigger, no notion of
"world already dense relative to chapters," no notion of an unused-entry backlog. Behavior digest
§7.1 says over-acting is the dominant failure; tech digest §6 wants a concrete checkable delta.
The Architect has neither. It also has **no `_fingerprint()` override**, so unlike Character
Keeper (`character_keeper.py:59-62`) it is not watermark-gated at all — it re-fires every
interval on an unchanged story, and its `readiness()` (`world_architect.py:30-32`) decays only
with raw entry count, never with story progress. This structurally biases it toward padding.

**D5 — No non-goals / lane boundaries.** Behavior digest §2.1 ("give every agent explicit
non-goals") is unmet. The prompt never says the Architect does not create characters (Keeper's
lane), does not author plot threads/secrets (Author's lane), and does not retcon existing
entries (Retconner's lane). WorldEntriesDraft has no thread/knowledge/character fields so the
lane leak can't happen through the schema — but a persona told to "expand history and cosmology"
with no boundary will happily write entries that assert plot events or invent named characters
inside `body`, which then leak into canon as un-owned facts.

**D6 — `supersedes_id` is a latent retcon footgun.** It sits on `WorldEntryDraft`
(`schemas.py:15`) and `commit()` writes entries straight through with no supersede handling
(`world_architect.py:58-60`) — the field is silently ignored on the create path. If the redesign
tells the Architect to "fix" contradictions it finds, it will reach for this field and quietly
retcon canon, stepping on the Retconner. The prompt must forbid it.

**D7 — Persona placement is fine but undirected.** The Architect is a canon-keeper; behavior
digest §5.1 says minimal persona (accuracy-first). Current code appends `In character:
{personality}` to the *task* message (`world_architect.py:44,46`), which is the light touch we
want — but there's no instruction separating analytical work from the in-voice `feed_note`
(behavior §5.2), so a thick personality can bleed into entry `body` prose.

---

## 2. Proposed system prompt

Paste-ready. Replaces `SYSTEM_PROMPT` in `world_architect.py:11-16`. Written as a role
specialization layered on the deepagents base + `RETRIEVAL_NOTE_BASE` (which the runner still
appends last, `world_architect.py:79`). It references the filesystem/`search_canon` tools by
name rather than re-explaining them (tech digest §1).

```python
SYSTEM_PROMPT = """You are the World Architect for a living, ever-expanding fictional world.
Your job is to grow the world's lore — its geography, factions, history, systems, and
cosmology — so the story always has grounded material to draw on. You are additive: you
expand the world, you never contradict what is already canon and never overwrite it.

## Your lane
- You create WORLD ENTRIES: places, factions, institutions, historical events, physical or
  metaphysical systems, cultures, and the rules that govern them.
- Good lore is STORY-SERVING. Prioritize expanding what recent chapters have touched but the
  canon does not yet cover — a place a scene visited, a faction a character named, a system
  the plot leaned on — over inventing disconnected regions no chapter needs. Grounded
  generativity beats encyclopedia-padding.

## Not your lane (do NOT do these)
- You do NOT write plot or narrate events. An entry describes what the world IS, not what
  happens in the story. Authoring chapters and plot threads is the Author's job.
- You do NOT create or name characters. Named people are the Character Keeper's canon. You may
  reference a faction or role, but never mint a person.
- You do NOT retcon or amend existing entries. If you find a contradiction, do NOT fix it and
  do NOT set supersedes_id — leave it blank. Repairing canon is the Retconner's job; the most
  you do is mention the conflict in your feed_note.

## How to work — research first, then emit
Work in two phases. Do NOT write entries from the pushed summary alone; it is an index, not the
source of truth.
1. SURVEY. Write a short todo list, then use your tools:
   - Read the most recent 3-5 chapters (grep/glob to locate them, then read_file) to see what
     places, factions, and systems the story is leaning on.
   - Use `search_canon` for thematic gaps ("what governs X?") and `grep` for exact names before
     you canonize anything.
   - List existing entries in the domain you're about to write (e.g. grep the world entries for
     the place/faction name). CONFIRM your entry does not duplicate or contradict one that
     exists. If it would, either drop it or narrow it to genuinely new, consistent material.
2. EMIT. Only after you have read the canon, return 1-3 new entries. For each, cite the
   evidence you grounded it in — the chapter or entry that shows the world needs it — in the
   entry's `sources` field (chapter titles/ids or entry titles you read). An entry you cannot
   ground is padding; cut it. Once you can cite why each entry is needed, stop searching and
   emit — do not keep browsing.

## Each entry
- title: a concrete, evocative name (not "The Northern Region").
- body: 2-4 paragraphs of specific lore that a chapter could be written against. Prose only —
  no headers, no bullet lists inside the body.
- domain: one of physical, social, metaphysical, historical, other.
- tags: a few lowercase topic tags.
- sources: the chapter/entry references that justify this entry (see phase 2).

Director seeds are always your work: when a seed is present, develop it into entries even if
you would otherwise stand aside.""" + PASS_PROMPT_INSTRUCTION + """
Standing aside is a real success, not a wasted pass — inventing lore the story does not need is
the failure. Set no_action=true when, after surveying, you find the world is already dense
enough to serve the recent chapters (existing entries cover the places/factions/systems the
story is using) and there is no director seed. But do NOT stand aside on a genuine gap: if a
recent chapter leans on something canon doesn't cover, you MUST fill it. Never set no_action
when a director seed is present — a seed is always your work."""
```

Notes on wording chosen to satisfy existing tests:
- Still ends with the "Never set no_action when a director seed is present" clause (matches the
  intent of the current final line, `world_architect.py:16`).
- The runner concatenation `SYSTEM_PROMPT + RETRIEVAL_NOTE_BASE` is unchanged, so
  `test_..._uses_retrieval_note_base` (`test_world_architect.py:138-148`), which asserts the sum
  ends with `RETRIEVAL_NOTE_BASE`, still passes.

---

## 3. Context-assembly changes

The push message should become a **lightweight map** (identifiers + a domain-grouped entry
index + a chapter index), with prose pulled just-in-time via tools (tech digest §3). Concretely,
change `poll()` and `work()` in `world_architect.py:34-48`:

**`poll()` — add chapters** (the single highest-impact change):
```python
async def poll(self) -> dict:
    return {
        "entries": await self._read.list_world_entries(),
        "chapters": await self._read.list_chapters(),
        "signals": await self._read.list_unconsumed_signals(target_agent=self.name),
        "hand": await self._read.get_active_hand(),
    }
```

**`work()` — push a domain-grouped entry index + a chapter index, not a flat 20-entry
truncation:**
```python
async def work(self, ctx: dict) -> WorldEntriesDraft | None:
    entries = ctx["entries"]
    # Group by domain so the agent can see coverage per domain at a glance
    # and grep for exact names before canonizing (consistency-before-emit).
    by_domain: dict[str, list[str]] = {}
    for e in entries:
        by_domain.setdefault(e.domain, []).append(e.title)
    existing = "\n".join(
        f"[{d}] " + "; ".join(titles) for d, titles in sorted(by_domain.items())
    ) or "The world is empty."
    chapters = chapter_map_note(ctx["chapters"])  # index only, never prose
    seeds = "\n".join(f"Director seed: {s.body}" for s in ctx["signals"]) or "None."
    cast = self._guarded_line("In character", self.personality)
    sparks = architect_settings_note(ctx.get("hand"))
    msg = (
        f"World entries so far ({len(entries)} total), by domain:\n{existing}\n\n"
        f"Chapter index (read the recent ones in full before writing):\n{chapters}\n\n"
        f"Director seeds:\n{seeds}{sparks}{cast}\n\n"
        "Survey the recent chapters and existing entries, then generate story-serving world "
        "entries — or stand aside if the world already covers what the story is using."
    )
    result = await self._runner.ainvoke({"messages": [{"role": "user", "content": msg}]})
    return result.get("structured_response")
```
Import `chapter_map_note` from `novelizer.brain.context` (already used by Author,
`author.py:3`). This reuses the pull-mode chapter index Author already ships
(`brain/context.py:85`): `[id] 'title' (status) cast: …` — enough for the Architect to pick
which chapters to `read_file`, without pushing any prose.

Rationale: the domain-grouped **title** index (not `body[:100]`) shows full coverage even at
50+ entries, so the consistency check in the prompt ("grep before canonize") has something to
grep against, and the chapter index gives the story grounding D1 is missing — while keeping the
static system prompt stable for prompt-cache hits (tech digest §1).

**Schema change (flagged — requires a schema + validator touch): add a `sources` field.** To
structurally force the tool loop against the constraint tax (tech digest §5: "require evidence
FIELDS in the output schema"), add to `WorldEntryDraft` (`schemas.py:10-23`):
```python
    sources: list[str] = Field(default_factory=list)  # chapter/entry refs grounding this entry
```
It defaults empty (so no existing test that constructs `WorldEntryDraft(...)` breaks) and
`commit()` can ignore it initially (the value is the *forcing function*, not a stored field) or
later persist it as provenance. This is the cheapest single lever to make the Architect actually
read before it writes. If a schema change is out of scope for this pass, the phase-ordering
prompt language in §2 is the fallback, but it is weaker without the field.

---

## 4. Behavioral guardrails

- **Pass / no_action calibration (verify-then-abstain, balanced).** The prompt now gives a
  concrete, checkable abstain criterion (behavior §7.2, tech digest §6): stand aside only after
  surveying and finding existing entries already cover what recent chapters use, and no seed is
  pending — with an explicit "don't miss a real gap" counter-clause (tech digest §6: pair
  verify-then-abstain with a don't-be-lazy clause). Abstention is named a success to counter the
  compulsion-to-act failure (behavior §7.1). The existing `commit()` honoring of `no_action`
  only when no seed is pending (`world_architect.py:51-56`) is correct and unchanged — the two
  tests at `test_world_architect.py:86,100` still hold.
- **Add a watermark so it doesn't re-fire on an unchanged story.** Override `_fingerprint()`
  (mirroring Character Keeper, `character_keeper.py:59-62`) so readiness is gated on real story
  deltas, not the clock (behavior §7.2, §7.4):
  ```python
  async def _fingerprint(self) -> tuple:
      chapters = await self._read.list_chapters()
      entries = await self._read.list_world_entries()
      signals = await self._read.list_unconsumed_signals(target_agent=self.name)
      return (len(chapters), chapters[-1].id if chapters else "", len(entries), len(signals))
  ```
  and gate readiness through it: `return await self._gate_on_watermark(max(0.2, 1.0 - count/50))`
  in `readiness()`. Captured after commits (`base.py:100-113`), so its own entry writes advance
  the watermark and don't re-trigger it; a new chapter or a new seed does. This turns "the world
  is dense, story hasn't moved" into a structural no-op instead of a padding pass.
- **Over-retrieval / turn-burning.** The prompt gives an explicit stop rule ("once you can cite
  why each entry is needed, stop searching and emit") (tech digest §3). With
  `GRAPH_RECURSION_LIMIT=100` (`base.py:19`) an un-stopped survey could burn the whole budget;
  the stop rule + the index-then-read loop bound it.
- **Constraint tax / premature emit.** Research-then-emit ordering plus the `sources` field
  (§3) are the two mitigations (tech digest §5). Without the field, the ordering language alone
  is the floor.
- **Lane boundaries.** Explicit non-goals for characters (Keeper), plot/threads/secrets
  (Author), and retcon/`supersedes_id` (Retconner) — the §2 "Not your lane" block. The schema
  already can't emit thread/knowledge/character intents, so the remaining leak vector is
  assertions inside `body`; the prompt forbids narrating events and minting people.
- **Structured-output pitfalls.** `no_action=true` must be paired with empty `entries`
  (`PASS_PROMPT_INSTRUCTION`, `base.py:25-29`) — unchanged. The `domain` validator already
  coerces out-of-enum values to `"other"` (`schemas.py:17-22`), so a hallucinated domain won't
  raise. `supersedes_id` left blank per the prompt keeps `commit()` on the pure-create path.

---

## 5. Persona / voice

The Architect is a **canon-keeper**, so persona stays light (behavior §5.1: heavy persona
degrades accuracy on knowledge tasks). Keep the current placement — `In character:
{personality}` appended to the *task* message (`world_architect.py:44,46`), not baked into the
system prompt — which is already the accuracy-first shape.

Add the §2 instruction "body: prose only, no headers/bullets" and keep the analytical survey
under neutral instructions, so the personality colors only the `feed_note`, not the entry
`body` (behavior §5.2: persona for the in-character note, plain instruction for the task). The
`feed_note` is the one place voice belongs — a one-line "another corner of the map, filled in"
in character (as `test_world_architect.py:73-83` exercises).

Chat persona (`personas.py:32-34`) is a thin, separate surface with no intent permissions and
shares no text with the autonomous prompt — correct as-is; the Architect commits world entries
only autonomously, and chat stays advisory. No change.

---

## 6. Risks & test hooks

**Tests that constrain wording / behavior (grepped `tests/`):**
- `test_world_architect.py:52-70` — asserts the push message contains `In character:` +
  personality when set, and omits it when unset. The `work()` rewrite (§3) keeps the
  `_guarded_line("In character", …)` call, so both pass. **Do not rename that label.**
- `test_world_architect.py:138-148` — asserts `(SYSTEM_PROMPT + RETRIEVAL_NOTE_BASE)` ends with
  `RETRIEVAL_NOTE_BASE` and that `RETRIEVAL_NOTE_BASE` lacks "chapter list below". The new
  SYSTEM_PROMPT changes text but not the concatenation order (runner still appends
  `RETRIEVAL_NOTE_BASE` last, `world_architect.py:79`), so this holds. Keeping the Architect on
  `RETRIEVAL_NOTE_BASE` (not the map-sentence `RETRIEVAL_NOTE`) is deliberate — the chapter
  index now lives in the user message instead.
- `test_world_architect.py:86-97` (no_action pass backs off) and `:100-113` (seed defeats
  pass) — depend on `commit()` logic (`world_architect.py:51-62`), which is unchanged. Hold.
- `test_world_architect.py:116-120` (readiness floor ≥ 0.2) — the watermark gate returns 0.0
  when the fingerprint is unchanged, which could drop readiness below 0.2 and **break this
  test**. Mitigation: the test calls `readiness()` immediately after a single `run_once()`
  without re-polling a changed story, so the fingerprint will differ from the initial `None`
  and the gate passes through the ≥0.2 floor on first read. Verify against the exact watermark
  timing; if it regresses, gate only the *upper* readiness (keep the 0.2 floor un-gated):
  `return max(0.2, await self._gate_on_watermark(1.0 - count/50))`.
- `test_muse/test_prompts.py:33-38` — pins `architect_settings_note` behavior. §3 keeps calling
  it unchanged (`world_architect.py:45`). Hold.
- `test_schemas` (referenced at `schemas.py:6`) pairs `_DOMAINS` with the store's `Domain`.
  Adding a `sources` field to `WorldEntryDraft` doesn't touch the domain enum, but re-run it.

**Regression risks:**
1. **Adding `chapters` to `poll()`** grows the poll cost and the push message. Bounded — the
   chapter index is one line per chapter (`brain/context.py:85`), no prose. Watch total tokens
   as the book grows; if the index gets long, push only the recent N and let the agent glob for
   older ones.
2. **`sources` schema field** is the only change outside `world_architect.py`. Default-empty
   keeps every existing `WorldEntryDraft(...)` construction valid. Low risk, high leverage; if
   deferred, note the constraint-tax mitigation weakens to prompt-only.
3. **Watermark gating** is the highest-risk behavioral change (readiness-floor test above, and
   it changes dispatch cadence). Land it behind the readiness-floor mitigation and confirm the
   Architect still bootstraps an empty world (empty world → new chapters advance fingerprint →
   fires).
4. **Prompt length** roughly triples. That's acceptable per tech digest §2 (minimalism ≠
   shortness — keep high-signal tokens), and it's static so prompt caching absorbs the cost
   (tech digest §1). Trim the §2 examples if attention-budget regressions show up in eval.
```
