# Output-Conventions Skill Pack — Design

**Date:** 2026-07-23
**Status:** Approved (brainstorm complete)

## Summary

Add a sixth skills pack, `novelizer/skills_packs/output-conventions/`, that
documents the mechanical contract for every structured-output field agents
emit — what belongs in `title` vs `prose`, length norms, no invented markup,
id-citing rules — plus a short shared prompt pointer so every tooled agent
knows the guide exists. This is prompt-side defense against degenerate
structured outputs; the motivating incident is Death Becomes Her chapter 1,
where the drafting model emitted its entire chapter (title line, an invented
`<prose>` tag, 8k characters of body, and a trailing JSON blob) into
`ChapterDraft.title`, which then polluted the canon virtual-FS filename
(`/chapters/001-<10k-char slug>.md`) and every downstream prompt that
interpolates the title.

## Background

Reference material for agents already ships as skill packs:
`novelizer/skills_packs/<pack>/SKILL.md` (+ `references/`), served read-only
at `/skills/` via `build_skills_backend()`
(`novelizer/canon_fs/skills_route.py`) inside the composite canon backend
(`Runtime._phase_a_toolkit`, `novelizer/runtime.py`). Five craft packs exist:
`outlining`, `promise-payoff`, `character-arcs`, `scene-sequel`, `pacing`.

Discovery today is uneven:

- **Author, Character Keeper, Plotter** pass `skills=CRAFT_SKILLS` to
  `create_deep_agent`, so deepagents' `SkillsMiddleware` advertises each
  pack's name + description in their system prompt (progressive disclosure —
  body read on demand).
- **Editor, Retconner, World Architect, Continuity Checker, Structure
  Analyst** (and other tooled agents) get the backend but no `skills=` —
  they can `read_file` `/skills/...` but nothing advertises it.

Because output conventions matter exactly at emit time — when an agent may
not think to go looking — discovery cannot rely on the middleware alone.
Hence the prompt pointer (below), following the established
`RETRIEVAL_NOTE` pattern (`novelizer/agents/prompts.py`, appended in each
tooled builder's `backend is not None` branch).

## Design

### 1. The pack: `novelizer/skills_packs/output-conventions/SKILL.md`

Frontmatter:

```yaml
---
name: output-conventions
description: The contract for every structured-output field — what belongs
  in title vs prose/body, length norms, no invented markup, id citing.
  Activate before emitting a draft, verdict, amendment, or intent if unsure
  what belongs in which field.
---
```

Body sections:

1. **Universal field rules** (applies to every schema)
   - Freeform text fields carry plain prose only: no invented markup tags
     (`<prose>`, `<title>`, XML/HTML of any kind), no markdown headers, no
     code fences, no JSON serialized into a string field.
   - Every field has exactly one job; content never doubles up across
     fields. If a field's content would repeat another field, stop and
     re-read the schema.
   - Titles and names are a single line. A newline in a title is always a
     mistake.
   - Every id cited (thread, secret, character, chapter, beat, promise)
     must be an id that appeared in the task context or in a canon file
     actually read this pass — never minted, never guessed from memory.
   - Empty-but-valid beats stuffed-but-wrong: when a field has a documented
     empty default (`""`, `[]`), emitting the default is correct when there
     is nothing real to say.

2. **Per-schema conventions** — one short subsection per major
   freeform-bearing schema:
   - `ChapterDraft`: `title` is one short line (≲ 120 characters,
     headline-style); the chapter body goes in `prose` and nowhere else;
     `prose` is narrative text only (no chapter-title line repeated at the
     top, no tags); `character_ids` come from the cast block ids;
     `feed_note` is a couple of sentences, not a report.
   - `WorldEntryDraft`: `title` short single line; `body` is the entry;
     `tags` are short lowercase tokens.
   - Keeper/Retconner amendments and `FlagDraft`: same title/body split;
     flag `detail` states the concern in 1–3 sentences with the evidence
     handle.
   - Intents (`ThreadIntent`, `PromiseIntent`, etc.): freeform `title`/name
     fields are short labels, and action-specific id fields follow the
     universal id rule.
   The section stays schema-shaped, not exhaustive: it covers the schemas
   whose freeform fields have bitten or plausibly could, and states the
   universal rules govern anything unlisted.

3. **Degenerate outputs — wrong vs right.** One vivid negative example
   modeled on the ch1 incident: a `ChapterDraft` whose `title` contains the
   title line + `<prose>` tag + full body + trailing JSON, shown wrong,
   followed by the same content emitted correctly (short `title`, body in
   `prose`, thread notes as `thread_intents`). One example, kept short —
   a concrete wrong-vs-right pair outperforms a rule list for smaller
   models.

No `references/` subdirectory at launch — the pack is self-contained until
content outgrows one file.

### 2. The prompt pointer

New constant in `novelizer/agents/prompts.py`, e.g.:

```python
OUTPUT_CONVENTIONS_NOTE = (
    "\n\n## Output contract\n"
    "Your structured output has a field-by-field contract: read "
    "/skills/output-conventions/SKILL.md before your first emit if you are "
    "unsure what belongs in which field. Titles are one short line; bodies "
    "go in body/prose fields; never invent markup tags."
)
```

Appended to the system prompt in each tooled builder's `backend is not None`
branch (the same place `RETRIEVAL_NOTE` is added today), across the tooled
agents that emit structured drafts: Author, Character Keeper, Plotter,
Editor, Retconner, World Architect, Continuity Checker, Structure Analyst.
Untooled builder branches are unchanged — without the backend the file is
unreadable, and the note's last sentence alone is not worth the tokens in
that mode.

The one-sentence inline summary ("Titles are one short line…") is
deliberate: it makes the note useful even when the agent never reads the
file.

### 3. Tests

Follow the existing shapes:

- `tests/skills_packs/test_pack_structure.py`: add `output-conventions` to
  `PACK_NAMES` — existing parametrized tests then cover SKILL.md existence,
  frontmatter name/description shape.
- `tests/canon_fs/test_skills_route.py` / `test_skills_seam.py`: extend
  whichever asserts the pack listing so `/skills/output-conventions` is
  visible through `build_skills_backend()`.
- Prompt-pointer tests: for each tooled builder, the built system prompt
  contains the note in the backend branch and not in the bare branch —
  matching however `RETRIEVAL_NOTE` inclusion is asserted today (extend
  those tests rather than inventing a new pattern).

## Out of scope

- Code-level validation of `ChapterDraft.title` (first-line/length cap at
  commit) and the slug length cap in `canon_fs/paths.py` — separate change,
  diagnosed 2026-07-22.
- Repairing Death Becomes Her ch1's stored title (blocked on an
  event-sourcing decision: `ChapterRevised` deliberately cannot change a
  title).
- Adding `skills=` middleware wiring to the tooled agents that lack it —
  the pointer makes the pack reachable without it; wiring more agents into
  `SkillsMiddleware` is an independent decision.

## Error handling

No new runtime failure modes: the pack is static packaged data behind the
existing read-only route, and the pointer is a string constant. A missing
or malformed SKILL.md is caught by the structure tests at CI time.
