# Continuity Checker — prompt redesign proposal

Scope: BOTH prompts on `novelizer/agents/continuity_checker.py` — the contradiction-review
`SYSTEM_PROMPT` (lines 19-24) and the prose-mining `MINING_SYSTEM_PROMPT` (lines 26-36).
Read-only diagnosis; every claim cites `file:line`.

The two prompts drive two structurally different runners and must be redesigned
differently:

- **Contradiction pass** — `build_continuity_checker_runner` (line 396). In `pull_mode`
  (`= s.checker_tools_enabled`, line 202) it gets a `backend` + filesystem tools and the
  Author's `RETRIEVAL_NOTE` is appended (line 409). `response_format=ContinuityOutput`
  (line 411, default tool-calling strategy). This is a **pull-mode JUDGE** — the tech
  digest's constraint-tax / research-then-emit / citation-grounding rules all apply.
- **Mining pass** — `build_continuity_mining_runner` (line 424). **No backend, no tools,
  single-shot**, `temperature=0.2` (line 434), `ProviderStrategy(MinedFactsOutput)`
  grammar-constrained decoding (line 442). Everything is pushed in one message
  (`_mining_prompt`, line 140). This is a **constrained EXTRACTOR** — tool-loop forcing is
  irrelevant; the lever here is worked examples + crisp field definitions.

---

## 1. Diagnosis

### Contradiction pass (`SYSTEM_PROMPT`, lines 19-24)

Current text:
> "You are the Continuity Checker for a living fictional world. Review the given world
> entries, characters, and chapter excerpts for contradictions, anachronisms, or logical
> inconsistencies. Return retcon_requests, each with a description (what contradicts what),
> conflicting_entry_ids (…), and a proposed_resolution. You may also be shown retcon
> requests already filed and still open: do not re-report those issues, even reworded.
> Return an empty list if you find nothing new." + PASS_PROMPT_INSTRUCTION

Concrete weaknesses:

1. **No non-goal / lane boundary (behavior Rule 2.1).** The prompt never says the checker
   *finds* contradictions but does NOT repair them — that is the Retconner's job
   (`retconner.py` consumes approved requests). `proposed_resolution` invites the model to
   draft the fix; without a boundary it drifts toward authoring corrections it has no
   authority to apply.
2. **Blind to its own deterministic backstops.** `commit()` unconditionally runs
   `find_leaks` (line 345) and `find_paradoxes` (line 358) and files those retcons every
   cycle, regardless of the LLM. The prompt says nothing about this, so the LLM spends
   attention hunting secret leaks and causal-ordering paradoxes that are **already caught
   by code** — pure wasted budget and a duplicate-flag source. Its unique value (what code
   cannot see: prose/sheet factual + timeline contradictions) is never named.
3. **No scrutiny weighting (behavior Rule 1.1).** "contradictions, anachronisms, or
   logical inconsistencies" is a flat, vibe-level ask. The evidence says errors cluster
   **mid-book** and the two dominant classes are **Timeline & Plot Logic** and **Factual &
   Detail Consistency** — the prompt should steer there, not at "anachronisms" in general.
4. **No grounding requirement (behavior Rule 3.1 — "no quote → no flag").** Nothing forces
   a quotation. `conflicting_entry_ids` is id-level grounding, not textual evidence. This
   is the single biggest over-flagging hole: the model can assert "X contradicts Y" from
   the truncated summaries alone.
5. **Constraint-tax exposure (tech digest §5).** `response_format=ContinuityOutput` biases
   the model to emit the structure early and **skip the tool loop** — in `pull_mode` this
   defeats the whole point of giving it file tools. There is no RESEARCH-then-EMIT
   ordering, no "don't file from the summary," and no stop rule. `RETRIEVAL_NOTE` (appended
   at line 409) is Author-authored — its map sentence ends "…before **writing**" (author.py
   line 24-25), which reads wrong for a checker and does not name a stopping criterion.
6. **Over-flagging not framed as the dominant failure (behavior Rule 7.1).** "Return an
   empty list if you find nothing new" is a weak permission, not a valued outcome. The
   research is explicit: false retcons **poison the Retconner's queue**; a correct empty
   pass should be framed as success, balanced by a "don't miss real ones" clause.
7. **Truncated push context presented as if authoritative.** `work()` inlines
   `world[:20]@200` (line 105), `chars[:10]` (line 106), and in non-pull mode
   `chapters[:300]` (line 112). The prompt implies these excerpts are the material to judge
   — but a 300-char prose slice cannot support a factual-contradiction claim. This is the
   same truncation-starvation family as the historical `prose[:300]` Character Keeper bug.

### Mining pass (`MINING_SYSTEM_PROMPT`, lines 26-36)

Current text describes the four fact kinds, the "cite existing ids only / known_id=False"
rule, the learn-vs-uses rule as a single sentence, "one short sentence" notes, and the
inspiration-facts addendum.

Concrete weaknesses:

1. **learn-vs-uses is stated as one abstract rule, no examples (tech digest §2 — examples
   lift parameter accuracy 72%→90%).** Lines 31-33 give the rule ("report 'learn' only
   when the chapter shows the moment of learning on the page") but the code comments record
   this is exactly what the live miner gets wrong. The digest is unambiguous: this
   subtle, load-bearing distinction needs 2-3 worked examples, not a sharper sentence.
2. **The secret-namespace confusion is fixed only in the user message, not the system
   prompt.** The `_mining_prompt` comment (lines 141-143) records the live miner "cited
   thread ids and character names as secret ids," patched by stating the namespace in the
   user message (line 167-168). The *system* prompt (the stable, cache-friendly surface)
   never explains what a secret id *is* versus a thread id / character name. The rule
   belongs in both places; systemically it is where the model forms its concept.
3. **`reveal` under-specified.** Line 28-29 lists "a secret being revealed" inline with the
   others, but reveal facts have a **separate schema arm** (`reveal_facts`, schemas.py 206)
   and **always escalate to a retcon** (lines 237-246) — they are categorically different
   from learn/uses. The prompt does not draw that line, so the model conflates "a secret is
   exposed in the open" (reveal) with "a character acts on a secret" (uses).
4. **`known_id=False` framed as a failure, not a first-class escape hatch.** The prompt
   says "set known_id=False if you cannot confidently match" (line 31) but doesn't tell the
   model this is the *correct, safe* move that routes the fact to human review rather than
   dropping it — so the model is tempted to force a wrong id to look complete.
5. **No "empty is success" framing (behavior Rule 7.1).** "Return empty lists if the prose
   shows nothing new" (line 33) is permission, not a valued outcome; the extractor should
   be told an empty result is a correct pass, and that inferring off-page events is a
   failure.

### Shared

- **Persona placement is already good on the mining side** (no persona in `_mining_prompt`
  — accuracy-first, behavior Rule 5.1) and acceptable on the contradiction side (`cast`
  line appended last, line 107). Neither prompt should grow a thick persona; the redesign
  keeps persona thin and confined to the feed_note.

---

## 2. Proposed system prompts (paste-ready)

### 2a. `SYSTEM_PROMPT` (contradiction pass)

Written as a role specialization on top of the deepagents base + the retrieval note; keeps
`+ PASS_PROMPT_INSTRUCTION` appended exactly as today (line 24). Tool *mechanics* are left
to the base and the retrieval note — the body names the motion and the grounding bar in
mode-agnostic language so it is still correct in the tool-less legacy path (where "read the
full passage" simply means "abstain," the safe direction).

```python
SYSTEM_PROMPT = """You are the Continuity Checker for a living novel that is written
chapter by chapter, without stopping. You FIND contradictions in the canon and file each
as a retcon_request. You do not repair them.

## Your lane, and the lanes that are not yours
You find contradictions; the Retconner fixes them. Never rewrite prose or lore yourself —
proposed_resolution is one line pointing at the fix, not the fix.

Two whole error classes are already caught for you by code every cycle; do NOT re-report
them:
- Secret leaks — a character using a secret they were never shown learning.
- Causal paradoxes — an effect chapter ordered before its cause.
Spend no attention there. Your unique value is what code cannot see: contradictions in the
actual prose and character/world sheets.

## What to look for
Weight your scrutiny toward the two classes that actually break long stories:
- Timeline & plot logic: dates, durations, ages, "three days later," an event that
  cannot have happened in the stated order.
- Factual & detail consistency: a name, an eye colour, a place, a quantity, a
  relationship stated one way here and another way there.
Contradictions cluster in the MIDDLE of a long manuscript and pile up with length. Read
toward mid-book chapters, not only the newest.

## How to work — research first, then file
1. Write a short todo list of what you will check this pass.
2. The context below is an index and short summaries, NOT the source of truth. Before you
   file anything, read the full passage on BOTH sides of the suspected conflict. Never file
   from a summary.
3. Ground every retcon in real quotations: the description MUST quote both conflicting
   spans and say where each one is (chapter title/heading or entry id). No quote, no flag —
   if you cannot cite both sides, you have not found a contradiction.
4. The moment you can cite both sides, stop searching and file it. Do not keep browsing.

## Output and when to stay silent
Each retcon_request: description (the two quoted spans + their locations + what conflicts),
conflicting_entry_ids (the ids of the conflicting records), proposed_resolution (one line).
You may be shown retcons already open — do not re-report those, even reworded.

A pass that files nothing is a SUCCESS, not a wasted turn. Inventing a marginal
contradiction to look busy poisons the Retconner's queue and is the failure this role most
often commits. The balance: if you CAN cite both sides of a real contradiction, you must
file it — staying silent on a genuine conflict is equally a failure.""" + PASS_PROMPT_INSTRUCTION
```

### 2b. `MINING_SYSTEM_PROMPT` (prose-mining pass)

No persona (accuracy-first). Adds worked examples for learn/uses/reveal, states the secret
namespace in the stable surface, and makes `known_id=False` and the empty result
first-class. The dealt-inspiration block still comes from the user message (`_mining_prompt`
lines 156-164) — the prompt only needs the reporting rule.

```python
MINING_SYSTEM_PROMPT = """You are the prose-mining pass of the Continuity Checker. You read
ONE chapter's full prose plus the current knowledge matrix, the active secret and thread
ids, the causal edges, and — if listed — the inspiration items dealt to this chapter. You
extract facts the prose plainly SHOWS on the page that the log has no covering event for.
You are an EXTRACTOR, not a judge: you do not decide whether anything is wrong, only report
what the prose depicts.

## What you may report
- secret_facts: a character learns a secret (action="learn") or acts on one it already
  holds (action="uses").
- reveal_facts: a secret is exposed in the open, to a room or a crowd.
- thread_facts: a plot thread is touched, planted into, or paid off.
- causal_facts: an event in one chapter causes an event in another.
- inspiration_facts: a dealt inspiration item the prose visibly uses.

## Cite existing ids only — never invent one
Every id you emit must already appear in the lists you were given; you never mint a new
secret or thread. Keep two namespaces separate:
- A SECRET id names a hidden fact (e.g. 'the-heir-lives'). ONLY ids in the "Active secret
  ids" list are legal secret ids. Thread ids and character names are NEVER secret ids.
- If a fact clearly fits but its id is not in the given list, that is fine: set
  known_id=false and report it anyway. That is the correct, safe move — it routes the fact
  to human review instead of dropping it or forcing a wrong id.

## learn vs uses — the distinction that matters most
Report "learn" ONLY when the chapter shows the moment of acquisition on the page — the
character overhears it, reads it, is told it, or works it out in the reader's view.
Report "uses" when the character ACTS on knowledge they already hold and no learning moment
is shown this chapter. When you are unsure which, it is "uses": a shown learning moment is
a high bar.

Worked examples:
- "Mara pressed the letter flat and read the single line: the heir lives. Her hands went
  cold." -> secret_fact action="learn" (acquisition happens on the page).
- "Kestrel walked straight to the third grave — the one no one had told her held the
  heir." -> secret_fact action="uses" (she acts on the secret; no learning is shown).
- "'The heir lives!' the herald cried across the square." -> reveal_fact (exposed in the
  open), NOT learn or uses.
- The chapter clearly advances a thread named in your thread list -> thread_fact
  action="touch" citing that thread id.

## Discipline
Report a fact only if the prose SHOWS it — never infer an offstage event, and never report
a fact the given matrix, references, or edges already cover. Keep every note to ONE short
sentence. Return empty lists when the prose shows nothing new: an empty result is a
correct, successful pass, not a failure. For inspiration_facts, only items from the dealt
list are legal — never invent one."""
```

**Should the two prompts share text?** No — keep them separate constants. They drive
different runners (tools vs no tools), different temperatures, different tasks (judge vs
extract). What they should share is a *design ethos*, not literal strings: cite-only-real-
ids, ground-in-the-prose, and empty-is-success. Merging them would force one prompt to
carry tool-loop guidance the mining runner cannot use, re-introducing the exact
attention-budget waste the tech digest warns against (§1, §2).

---

## 3. Context-assembly changes

The user-message builders are mostly right; two constraints are load-bearing and must be
preserved (see §6): the `In character` line in `work()` (line 107) and the dealt-items
block in `_mining_prompt` (lines 156-164).

**Contradiction pass (`work()`, lines 104-115):**

- Keep the pull-mode chapter *index* (line 110). It is a correct lightweight map. But make
  the index locatable by position so "mid-book" is targetable: have `chapter_map_note`
  (context.py line 85) prefix an ordinal, e.g. `- [3/12] [id] 'title' …`. Small change,
  directly serves behavior Rule 1.1. *(Shared-surface: `chapter_map_note` is Author-shared;
  flag as a shared change — verify no test pins its exact line format before shipping.)*
- Leave `world[:20]@200` and `chars[:10]` as index rows, but the new SYSTEM_PROMPT reframes
  them as summaries-not-truth, so their truncation stops being a correctness hazard.
- **Non-pull legacy path is the weak spot.** With no tools and `chapters[:300]` (line 112),
  the "read the full passage before filing" instruction degrades to "abstain," which is
  safe but blind. Recommendation: treat `pull_mode` as the supported path (memory records
  all agents are now tooled) and, if the legacy path must remain, raise its prose budget or
  accept that it can only catch contradictions visible within the pushed slice.

**Shared retrieval note (flagged shared-surface change):** the contradiction runner appends
the Author's `RETRIEVAL_NOTE` (line 409), whose map sentence ends "…before writing"
(author.py 24-25). Propose a checker-specific note built from the same
`_RETRIEVAL_NOTE_PREFIX`/`_SUFFIX` parts, swapping the middle sentence for: *"The chapter
list below is an index — read the full passage on both sides of any suspected conflict
before filing, and stop searching once you can cite both."* This adds the stop rule at the
tool-instruction layer where it belongs and removes the author-voiced "writing." Keep
`RETRIEVAL_NOTE_BASE`/`RETRIEVAL_NOTE` intact for the Author; add `CHECKER_RETRIEVAL_NOTE`
alongside them.

**Mining pass (`_mining_prompt`, lines 140-171):** structurally correct — full prose is
pushed because the runner has no tools, and the namespace/matrix/refs/threads/causal/dealt
blocks are all present. Two low-risk readability tweaks (tech digest §4, natural-language
ids aid precision):
- The matrix line renders `known_by` as raw character ids (line 146-147). Consider glossing
  with names, e.g. `known_by=[mara (Mara)]`, since the model reasons over who-knows-what.
  Optional; ids are already human-readable slugs, so low priority.
- Keep `secret_ids` stated outright (line 144, 167-168) — it is the live fix and the new
  system prompt now reinforces it rather than replacing it.

---

## 4. Behavioral guardrails

**Pass / no_action calibration (contradiction pass).** The plumbing is already correct:
`note_pass()` fires only when `out.no_action` AND no mined facts AND
`deterministic_filed == 0` (lines 374-377), so a pass that the detectors filled does not
wrongly back off. The prompt change supplies the missing half — verify-then-abstain
(behavior Rule 7.1 / tech digest §6): file only a citable contradiction, treat an empty
pass as success, balanced by the "you must file a real one" clause. `no_action=true` +
empty lists is handled by the appended `PASS_PROMPT_INSTRUCTION` (base.py 25-29) — kept.

**Over-retrieval / constraint tax (contradiction pass).** Two opposing failure modes, both
addressed:
- *Premature finalization* (constraint tax from `response_format`, tech digest §5): the
  RESEARCH-then-FILE ordering + "never file from a summary" + the quote-both-sides
  requirement structurally force the tool loop — the schema cannot be satisfied without
  first reading, because the description must contain real quotations.
- *Turn-burning*: the "stop the moment you can cite both sides" rule is the stopping
  criterion against browsing until the recursion limit (100, base.py 19).
- **Stronger option (flagged schema change):** add `evidence: list[str]` (file:line/quote)
  to `RetconDraft` (schemas.py 126) so grounding is a required field, not a description
  convention. Higher cost — touches `RetconRequest`, the commit path (lines 340-342), and
  `open_retcons_note` dedup. Recommend the description-embedded quote first; adopt the field
  only if quotes-in-description prove unreliable in eval.

**Lane boundaries (behavior Rule 2.1).** Named explicitly now: does NOT rewrite (Retconner
owns repair); does NOT re-report leaks/paradoxes (the deterministic detectors own those,
lines 345/358); the contradiction pass and mining pass do not do each other's job.

**Mining guardrails.** Extractor-not-judge stance; "when unsure, uses" tie-breaker (protects
the leak-catch path — see §6); `known_id=false` as the safe escape hatch, not a failure;
"never infer offstage events"; empty-is-success. The commit-side safety nets stay as the
backstop: ambiguous/unknown ids escalate to a tagged retcon (lines 207-217, 250-259),
thread-id-in-secret-slot is redirected to a touch (lines 186-206), reveals always escalate
(lines 237-246), inspiration items outside the dealt pool are dropped (lines 289-297). The
prompt should reduce how often those fire, not replace them.

**Structured-output pitfalls.** Both schemas stay lean (tech digest §5) — no new required
fields in the primary recommendation. The mining runner keeps `ProviderStrategy` +
temperature 0.2 (lines 434-442); the examples steer the *values* chosen inside the grammar,
they do not fight it.

---

## 5. Persona / voice

Behavior Rule 5.1: personas help voice/alignment but **hurt factual accuracy** — so on a
fact-checking agent, persona must be thin and confined to the feed_note.

- **Mining pass: no persona, keep it that way.** `_mining_prompt` injects none (correct);
  do not add one. Pure extraction under neutral instructions.
- **Contradiction pass: persona stays a single appended line.** `cast = _guarded_line("In
  character", self.personality)` (line 107) is appended *after* the analytical instructions
  — the analysis runs neutral, the voice only colours the output. Keep exactly this shape
  (also a hard test constraint, §6). The new SYSTEM_PROMPT does the analytical work under
  neutral instruction and leaves voice to the `feed_note`, matching behavior Rule 5.2.
- **feed_note:** one line, in voice, rendered *after* the analysis — the `_remark` path
  (lines 343, 375) already treats it as a surface line, not part of the judgement.
- **Chat persona** (personas.py 39-41): "You are the Continuity Checker — you hunt
  contradictions, leaks, and drift across the manuscript." Thin and fine; it shares no text
  with the autonomous prompt by design (architecture brief §chat). Leave it. Minor note:
  the autonomous agent now *delegates* leak detection to code, but for the interactive chat
  surface "leaks and drift" is still an accurate description of the domain — no change
  needed.

---

## 6. Risks & test hooks

**Hard wording constraints (must preserve):**
- `tests/agents/test_continuity_checker.py:63-64` — asserts the personality substring and
  `"In character:"` appear in the contradiction user message. Preserved: `work()` line 107
  is unchanged; I rewrite only the `SYSTEM_PROMPT` constant.
- `tests/agents/test_guarded_line_adoption.py:8` — `continuity_checker` must use the `"In
  character"` label. Preserved (same reason).
- `tests/agents/test_continuity_uptake.py:56-57` — the mining user message must contain the
  dealt items `"glazier"` and `"a debt is called in early"`. Preserved: `_mining_prompt`
  lines 156-164 are unchanged; I rewrite only the `MINING_SYSTEM_PROMPT` constant.

**No test pins the two system-prompt constants' exact text** (grep of `tests/` shows all
assertions are on the built user messages, retcon outcomes, and schema types) — the
rewrites in §2 are free to ship as-is.

**Regression risks:**
1. *"Don't re-report leaks/paradoxes" could suppress a real contradiction that overlaps a
   leak.* Mitigated by wording: the classes to skip are precisely "leak" and "ordering
   paradox"; a factual/timeline conflict that is *more than* those is explicitly still in
   scope. Watch eval for missed conflicts that happen to touch a secret.
2. *Live mining tests depend on the learn/uses call.*
   `tests/agents/test_prose_mining_live_llm.py` engineers Kestrel *acting on* a secret only
   Mara knows, with no reveal — the correct mining output is a `uses` fact for Kestrel,
   which `find_leaks` then catches. The new "when unsure, uses" tie-breaker and the Kestrel
   "walked straight to the grave" example (§2b) point **directly at this scenario** and
   should *lower* its flakiness, not raise it. STAGE 1 of that test (line 117-123) is the
   canary.
3. *Shared-surface changes* (§3): the `chapter_map_note` ordinal and the new
   `CHECKER_RETRIEVAL_NOTE` touch surfaces used or paralleled by the Author. Before
   shipping, grep for tests pinning `chapter_map_note`'s format and confirm
   `RETRIEVAL_NOTE`/`RETRIEVAL_NOTE_BASE` remain byte-identical for the Author. Ship these
   as separate, independently-revertable commits from the prompt rewrites.
4. *Optional `evidence` schema field* (§4) is the highest-blast-radius change (schemas.py,
   RetconRequest, commit path, dedup, tests). Not in the primary recommendation; gate it
   behind eval evidence that description-embedded quotes are insufficient.

**Test hooks to add (recommended):** a `test_system_prompt_names_its_non_goals`-style unit
asserting the contradiction prompt contains the Retconner boundary and the "do not
re-report leaks/paradoxes" clause; and a mining-prompt unit asserting the learn/uses
examples are present — cheap guards so a future edit cannot silently delete the
load-bearing guidance.
