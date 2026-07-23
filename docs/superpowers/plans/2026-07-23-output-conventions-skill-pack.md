# Output-Conventions Skill Pack Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a sixth skills pack (`output-conventions`) documenting the structured-output field contract, plus a shared prompt pointer appended to all eight tooled agent builders.

**Architecture:** Static pack content under `novelizer/skills_packs/output-conventions/` served by the existing read-only `/skills/` route (zero backend changes); one new string constant in `novelizer/agents/prompts.py`; a one-line append in each tooled builder's `backend is not None` branch, mirroring how `RETRIEVAL_NOTE`/`RETRIEVAL_NOTE_BASE` is appended today.

**Tech Stack:** Python 3.13, pydantic schemas, deepagents `create_deep_agent`, pytest (async mode auto). Run tests with `uv run pytest` from the worktree root.

**Spec:** `docs/superpowers/specs/2026-07-23-output-conventions-skill-pack-design.md`

## Global Constraints

- SKILL.md frontmatter is parsed by a NAIVE line-based YAML parser (`tests/skills_packs/test_pack_structure.py::_parse_simple_yaml` splits each line on the first `:`): `name:` and `description:` MUST each be a single physical line. No multi-line YAML values.
- SKILL.md body ≤ 2000 words (`test_body_word_count_within_budget`).
- Every pack MUST have a `references/` directory with ≥ 1 non-empty file (`test_references_nonempty`). This is a deliberate deviation from the spec's "no references/ at launch" line — the existing test contract requires one, so the per-schema field tables live there.
- Run pytest in the FOREGROUND, never backgrounded, and never in the main checkout — only in this worktree.
- Do not touch `novelizer/canon_fs/` code — the `/skills/` route serves any directory present in `novelizer/skills_packs/` automatically.
- Commit messages end with `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

---

### Task 1: The `output-conventions` pack + structure/route/seam tests

**Files:**
- Create: `novelizer/skills_packs/output-conventions/SKILL.md`
- Create: `novelizer/skills_packs/output-conventions/references/schema-conventions.md`
- Modify: `tests/skills_packs/test_pack_structure.py:14-20` (PACK_NAMES list)
- Modify: `tests/canon_fs/test_skills_route.py:42-56` (`test_skills_ls_lists_five_packs`)
- Modify: `tests/canon_fs/test_skills_seam.py:31-37` (EXPECTED_PACKS set)

**Interfaces:**
- Consumes: existing `/skills/` route (`build_skills_backend()`), untouched.
- Produces: pack directory name `output-conventions`, pack file path `/skills/output-conventions/SKILL.md` (Task 2's note constant and Task 3's tests reference this exact path).

- [ ] **Step 1: Extend the three test files (failing first)**

In `tests/skills_packs/test_pack_structure.py`, add the new pack to `PACK_NAMES`:

```python
PACK_NAMES = [
    "outlining",
    "promise-payoff",
    "character-arcs",
    "scene-sequel",
    "pacing",
    "output-conventions",
]
```

In `tests/canon_fs/test_skills_route.py`, rename `test_skills_ls_lists_five_packs` to `test_skills_ls_lists_all_packs` and add the new entry:

```python
async def test_skills_ls_lists_all_packs(stack):
    _events, proj, read = stack
    await proj.catch_up()
    composite = build_composite(read)
    result = await composite.als("/skills")
    paths = {e["path"] for e in result.entries}
    expected = {
        "/skills/outlining",
        "/skills/promise-payoff",
        "/skills/scene-sequel",
        "/skills/character-arcs",
        "/skills/pacing",
        "/skills/output-conventions",
    }
    # entries may have trailing slash for directories
    normalized = {p.rstrip("/") for p in paths}
    assert expected <= normalized
```

In `tests/canon_fs/test_skills_seam.py`, add to `EXPECTED_PACKS`:

```python
EXPECTED_PACKS = {
    "outlining",
    "promise-payoff",
    "character-arcs",
    "scene-sequel",
    "pacing",
    "output-conventions",
}
```

(The seam test's docstrings/messages say "five packs" — update the two f-string messages in that file from "all five packs" to "all packs"; leave docstrings alone.)

- [ ] **Step 2: Run to verify failures**

Run: `uv run pytest tests/skills_packs/test_pack_structure.py tests/canon_fs/test_skills_route.py tests/canon_fs/test_skills_seam.py -v`
Expected: FAIL — parametrized `output-conventions` cases fail with "missing output-conventions/SKILL.md"; the ls and seam tests fail on the missing pack name.

- [ ] **Step 3: Create `novelizer/skills_packs/output-conventions/SKILL.md`**

Exact content (frontmatter description is ONE line — do not wrap it):

````markdown
---
name: output-conventions
description: The contract for every structured-output field — what belongs in title vs prose/body, length norms, no invented markup, id citing. Activate before emitting a draft, verdict, amendment, or intent if unsure what belongs in which field.
---

# Output conventions

Your final structured output is parsed by machines, projected into canon
files, and interpolated into other agents' prompts. A malformed field does
not fail loudly — it silently pollutes the canon filesystem and every
downstream context. These are the field-by-field rules.

## Universal rules (every schema)

- **Plain text only in freeform fields.** No invented markup tags
  (`<prose>`, `<title>`, any XML/HTML), no markdown headers, no code
  fences, no JSON serialized into a string field. If you find yourself
  writing a tag inside a field, stop: the schema already separates the
  parts — use the fields.
- **One field, one job.** Content never doubles up across fields. The
  title never contains the body; the body never repeats the title; a
  notes field never carries a second copy of your intents.
- **Titles and names are a single line.** A newline in a title or name
  is always a mistake. Aim under 120 characters, headline-style.
- **Cite only ids you saw.** Every id you emit (thread, secret,
  character, chapter, beat, promise) must appear in your task context or
  in a canon file you actually read this pass. Never mint an id, never
  reconstruct one from memory.
- **Empty defaults are valid answers.** When a field documents an empty
  default (`""`, `[]`), emitting that default is correct when there is
  nothing real to say. Padding a list to look thorough is a failure.

## Per-schema conventions

Read `references/schema-conventions.md` for the field-by-field table of
the major schemas (ChapterDraft, WorldEntryDraft, keeper outputs, flags,
intents). The universal rules above govern any schema not listed there.

The schema that has actually failed in production, in brief:

- `ChapterDraft.title` — one short line, the chapter's name only.
- `ChapterDraft.prose` — the entire chapter body, and nothing else: no
  repeated title line at the top, no tags, no trailing notes.
- `ChapterDraft.feed_note` — a couple of sentences for the feed, not a
  report.
- Thread/theme/promise observations belong in their intent lists, never
  appended to `title` or `prose` as text.

## Degenerate output — wrong vs right

An actual production failure. The drafting model emitted this
`ChapterDraft`:

```json
{
  "title": "The First Change\n<prose>\nThe decision arrived without fanfare. [7,900 more characters of chapter body] </prose>\n[{\"intent\": \"plant\", \"name\": \"uneven reception across places\", \"note\": \"...\"}]",
  "prose": "The decision arrived without fanfare. ...",
  "thread_intents": []
}
```

Everything went into `title`: the real title, an invented `<prose>` tag,
the full chapter body, and a JSON blob of thread notes. Downstream, the
canon file for this chapter became
`/chapters/001-the-first-change-prose-the-decision-arrived-…` — a
10,000-character filename — and every agent's prompt carried the whole
chapter twice.

The same content, emitted correctly:

```json
{
  "title": "The First Change",
  "prose": "The decision arrived without fanfare. ...",
  "thread_intents": [
    {"action": "plant", "name": "uneven reception across places", "note": "Some places welcome Death's transition; others resist it.", "evidence": ""}
  ]
}
```

One field, one job. When in doubt, re-read the schema before you emit.
````

- [ ] **Step 4: Create `novelizer/skills_packs/output-conventions/references/schema-conventions.md`**

Exact content:

````markdown
# Per-schema field conventions

Field names below are exact (`novelizer/agents/schemas.py`,
`novelizer/agents/base.py`). "One line" means no newlines, headline-style,
aim under 120 characters.

## ChapterDraft (Author)

| Field | Contract |
|---|---|
| `title` | One line: the chapter's name only. Never the body, never tags. |
| `prose` | The entire chapter body, and nothing else. No repeated title line, no markup, no trailing notes. |
| `character_ids` | Ids from the cast block of your task context. |
| `feed_note` | 1–3 sentences for the activity feed. |
| `thread_intents` etc. | Structured observations go here, never as text inside `title`/`prose`. |
| `flags` | Concerns you cannot resolve yourself, as `FlagDraft` items. |

## WorldEntryDraft (World Architect, Retconner)

| Field | Contract |
|---|---|
| `title` | One line, the entry's name. |
| `body` | The entry text, plain prose. |
| `domain` | One of the documented enum values; when unsure, `other`. |
| `tags` | Short lowercase tokens, not sentences. |
| `supersedes_id` | Only an id of an entry you actually read. |

## KeeperOutput (Character Keeper)

| Field | Contract |
|---|---|
| `new_characters[].name` | One line, the character's name only. |
| `new_characters[].traits/motivations/backstory/voice` | Plain prose, each field its own concern — don't repeat one field inside another. |
| `updated_characters[].id` | An existing character id from cast context or a read file. |
| `feed_note` / `no_action` | Stand-asides: `no_action=true`, empty lists, one-line `feed_note`. |

## FlagDraft (all agents)

| Field | Contract |
|---|---|
| `category` | The category your role documents. |
| `description` | 1–3 sentences stating the concern with its evidence handle. |
| `related_entry_ids` | Only ids you saw. |
| `proposed_resolution` | One sentence; empty when you have none. |

## Intents (ThreadIntent, PromiseIntent, ThemeIntent, KnowledgeIntent, CausalIntent)

- Minting actions (`plant`, `make`, `introduce`) fill the freeform
  `name`/`title` — one line, a label not a paragraph — and leave `id`
  empty: the system slugs the id.
- Citing actions (`touch`, `pay_off`, `abandon`, `progress`, `pay`,
  `release`, `develop`, `learn`, `reveal`, `uses`) fill `id` with an id
  you saw, and leave `name`/`title` empty.
- `note` is one or two sentences of context, not a summary of the
  chapter.
- `evidence` (where present) is a chNNN handle or canon file path you
  actually read — a citing intent without evidence reads as a guess.
- `CausalIntent` cites two existing chapter ids; it never mints anything.

## SummarizerOutput

| Field | Contract |
|---|---|
| `gist` | A single line, ≤ 140 characters, for the chapter map. |
| `summary` | One paragraph. |
````

- [ ] **Step 5: Run to verify all pass**

Run: `uv run pytest tests/skills_packs/ tests/canon_fs/test_skills_route.py tests/canon_fs/test_skills_seam.py -v`
Expected: PASS (all parametrized cases across the six packs; ls and seam tests see six packs).
Note: `tests/skills_packs/test_outlining_framework_keys.py` runs in this scope too and must stay green (it only reads the outlining pack; no changes expected).

- [ ] **Step 6: Commit**

```bash
git add novelizer/skills_packs/output-conventions tests/skills_packs/test_pack_structure.py tests/canon_fs/test_skills_route.py tests/canon_fs/test_skills_seam.py
git commit -m "feat(skills): output-conventions pack — structured-output field contract

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: `OUTPUT_CONVENTIONS_NOTE` constant

**Files:**
- Modify: `novelizer/agents/prompts.py` (append after `RETRIEVAL_NOTE`, line 49)
- Test: `tests/agents/test_prompts.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `novelizer.agents.prompts.OUTPUT_CONVENTIONS_NOTE: str` — Task 3 imports exactly this name from exactly this module.

- [ ] **Step 1: Write the failing tests**

Add a new class to `tests/agents/test_prompts.py` (and add `OUTPUT_CONVENTIONS_NOTE` to the existing `from novelizer.agents.prompts import (...)` block):

```python
class TestOutputConventionsNote:
    def test_is_a_self_contained_section(self):
        """Appended after other notes, so it must open its own heading."""
        assert OUTPUT_CONVENTIONS_NOTE.startswith("\n\n## Output contract\n")

    def test_points_at_the_pack_file(self):
        """The pointer must name the exact readable path, not just the pack."""
        assert "/skills/output-conventions/SKILL.md" in OUTPUT_CONVENTIONS_NOTE

    def test_carries_the_inline_summary(self):
        """Useful even when the agent never reads the file."""
        assert "one short line" in OUTPUT_CONVENTIONS_NOTE
        assert "markup" in OUTPUT_CONVENTIONS_NOTE
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/agents/test_prompts.py -v`
Expected: FAIL with `ImportError: cannot import name 'OUTPUT_CONVENTIONS_NOTE'`

- [ ] **Step 3: Add the constant to `novelizer/agents/prompts.py`**

Insert after the `RETRIEVAL_NOTE = ...` line (line 49), before `DEFAULT_PASS_REMARK`:

```python
# Appended in every tooled builder's backend branch (same seam as the
# retrieval notes). The last sentence is a deliberate inline summary: the
# note must do some good even when the agent never opens the file.
OUTPUT_CONVENTIONS_NOTE = (
    "\n\n## Output contract\n"
    "Your structured output has a field-by-field contract: read "
    "/skills/output-conventions/SKILL.md before your first emit if you are unsure "
    "what belongs in which field. The short version: titles and names are one "
    "short line; bodies go in prose/body fields and nowhere else; never invent "
    "markup tags inside a field; cite only ids you actually saw."
)
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/agents/test_prompts.py -v`
Expected: PASS (new class green, existing classes untouched and green).

- [ ] **Step 5: Commit**

```bash
git add novelizer/agents/prompts.py tests/agents/test_prompts.py
git commit -m "feat(prompts): OUTPUT_CONVENTIONS_NOTE pointer constant

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: Append the note in all eight tooled builders

**Files:**
- Modify: `novelizer/agents/author.py:371`
- Modify: `novelizer/agents/character_keeper.py:381`
- Modify: `novelizer/agents/plotter.py:307`
- Modify: `novelizer/agents/editor.py:308`
- Modify: `novelizer/agents/retconner.py:182`
- Modify: `novelizer/agents/world_architect.py:171`
- Modify: `novelizer/agents/continuity_checker.py:549`
- Modify: `novelizer/agents/structure_analyst.py:186`
- Test (create): `tests/agents/test_output_conventions_note.py`

**Interfaces:**
- Consumes: `from novelizer.agents.prompts import OUTPUT_CONVENTIONS_NOTE` (Task 2); pack path `/skills/output-conventions/SKILL.md` (Task 1).
- Produces: nothing downstream; behavior change only.

- [ ] **Step 1: Write the failing test file**

Create `tests/agents/test_output_conventions_note.py`:

```python
"""Every tooled builder appends OUTPUT_CONVENTIONS_NOTE in its backend
branch and omits it in the bare branch.

The existing per-builder tests assert note inclusion by string composition
only; here we capture the system_prompt actually handed to
deepagents.create_deep_agent (imported at call time inside each builder,
so patching the deepagents module attribute intercepts all of them).
"""
from __future__ import annotations

import deepagents
import pytest

from novelizer.agents.prompts import OUTPUT_CONVENTIONS_NOTE
from novelizer.canon_fs.backend import CanonBackend

BUILDERS = [
    ("novelizer.agents.author", "build_author_runner"),
    ("novelizer.agents.character_keeper", "build_character_keeper_runner"),
    ("novelizer.agents.plotter", "build_plotter_runner"),
    ("novelizer.agents.editor", "build_editor_runner"),
    ("novelizer.agents.retconner", "build_retconner_runner"),
    ("novelizer.agents.world_architect", "build_world_architect_runner"),
    ("novelizer.agents.continuity_checker", "build_continuity_checker_runner"),
    ("novelizer.agents.structure_analyst", "build_structure_analyst_runner"),
]


class _FakeSettings:
    agent_model = "gpt-4o-mini"
    author_model = "gpt-4o-mini"
    llm_base_url = None
    llm_api_key = "test-key"
    agent_temperature = 0.7
    author_temperature = 0.8
    llm_max_tokens = None


class _FakeGraph:
    def with_config(self, config):
        return self


@pytest.fixture
def captured_prompt(monkeypatch):
    captured: dict = {}

    def fake_create_deep_agent(*args, **kwargs):
        captured["system_prompt"] = kwargs.get("system_prompt")
        return _FakeGraph()

    monkeypatch.setattr(deepagents, "create_deep_agent", fake_create_deep_agent)
    return captured


def _build(module_name: str, func_name: str, **kwargs):
    import importlib

    module = importlib.import_module(module_name)
    return getattr(module, func_name)(_FakeSettings(), **kwargs)


@pytest.mark.parametrize("module_name,func_name", BUILDERS)
def test_backend_branch_appends_note(module_name, func_name, captured_prompt):
    _build(
        module_name, func_name,
        backend=CanonBackend(read_store=None), tools=[],
    )
    prompt = captured_prompt["system_prompt"]
    assert prompt is not None, "builder never passed system_prompt"
    assert OUTPUT_CONVENTIONS_NOTE in prompt
    # Appended, not injected mid-prompt: the note is a trailing section.
    assert prompt.endswith(OUTPUT_CONVENTIONS_NOTE)


@pytest.mark.parametrize("module_name,func_name", BUILDERS)
def test_bare_branch_omits_note(module_name, func_name, captured_prompt):
    _build(module_name, func_name)
    prompt = captured_prompt["system_prompt"]
    assert prompt is not None, "builder never passed system_prompt"
    assert OUTPUT_CONVENTIONS_NOTE not in prompt
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/agents/test_output_conventions_note.py -v`
Expected: 8 FAIL (`test_backend_branch_appends_note` — note missing), 8 PASS (`test_bare_branch_omits_note`). If a bare-branch case ERRORs instead (fake settings missing an attribute), fix `_FakeSettings`, not the builder.

- [ ] **Step 3: Edit the eight builders**

In each file, add the import `from novelizer.agents.prompts import OUTPUT_CONVENTIONS_NOTE` (top-level, with the other novelizer imports), then change the backend-branch `system_prompt` line. Exact old → new for each:

`novelizer/agents/author.py:371`
```python
# old
        system_prompt = AUTHOR_SYSTEM_PROMPT + RETRIEVAL_NOTE
# new
        system_prompt = AUTHOR_SYSTEM_PROMPT + RETRIEVAL_NOTE + OUTPUT_CONVENTIONS_NOTE
```

`novelizer/agents/character_keeper.py:381`
```python
# old
        system_prompt = SYSTEM_PROMPT + KEEPER_PULL_NOTE
# new
        system_prompt = SYSTEM_PROMPT + KEEPER_PULL_NOTE + OUTPUT_CONVENTIONS_NOTE
```

`novelizer/agents/plotter.py:307`
```python
# old
        system_prompt = PLOTTER_SYSTEM_PROMPT + RETRIEVAL_NOTE_BASE
# new
        system_prompt = PLOTTER_SYSTEM_PROMPT + RETRIEVAL_NOTE_BASE + OUTPUT_CONVENTIONS_NOTE
```

`novelizer/agents/editor.py:308`
```python
# old
        system_prompt = SYSTEM_PROMPT + RETRIEVAL_NOTE_BASE
# new
        system_prompt = SYSTEM_PROMPT + RETRIEVAL_NOTE_BASE + OUTPUT_CONVENTIONS_NOTE
```

`novelizer/agents/retconner.py:182`
```python
# old
        system_prompt = SYSTEM_PROMPT + RETRIEVAL_NOTE_BASE
# new
        system_prompt = SYSTEM_PROMPT + RETRIEVAL_NOTE_BASE + OUTPUT_CONVENTIONS_NOTE
```

`novelizer/agents/world_architect.py:171`
```python
# old
        system_prompt = SYSTEM_PROMPT + RETRIEVAL_NOTE_BASE
# new
        system_prompt = SYSTEM_PROMPT + RETRIEVAL_NOTE_BASE + OUTPUT_CONVENTIONS_NOTE
```

`novelizer/agents/continuity_checker.py:549`
```python
# old
        system_prompt = SYSTEM_PROMPT + RETRIEVAL_NOTE
# new
        system_prompt = SYSTEM_PROMPT + RETRIEVAL_NOTE + OUTPUT_CONVENTIONS_NOTE
```
(continuity_checker imports `RETRIEVAL_NOTE` inside the builder function at line 537; add the `OUTPUT_CONVENTIONS_NOTE` import at module top level anyway, matching the instruction above.)

`novelizer/agents/structure_analyst.py:186`
```python
# old
        system_prompt = SYSTEM_PROMPT + RETRIEVAL_NOTE_BASE
# new
        system_prompt = SYSTEM_PROMPT + RETRIEVAL_NOTE_BASE + OUTPUT_CONVENTIONS_NOTE
```

Line numbers are as of commit b5ac3f5 — match on the code, not the number, if drift occurred.

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/agents/test_output_conventions_note.py -v`
Expected: 16 PASS.

- [ ] **Step 5: Run the neighboring agent test files for regressions**

Run: `uv run pytest tests/agents/ -x -q`
Expected: PASS. If `test_editor_prompt_byte_identical_to_pre_m3_3_shape_when_brain_silent` or any prompt-shape test fails, it is asserting on the UNTOOLED prompt path and must not be affected — investigate before touching it; do not update snapshots blindly.

- [ ] **Step 6: Commit**

```bash
git add novelizer/agents/author.py novelizer/agents/character_keeper.py novelizer/agents/plotter.py novelizer/agents/editor.py novelizer/agents/retconner.py novelizer/agents/world_architect.py novelizer/agents/continuity_checker.py novelizer/agents/structure_analyst.py tests/agents/test_output_conventions_note.py
git commit -m "feat(agents): append OUTPUT_CONVENTIONS_NOTE in all eight tooled builders

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: Full verification sweep

**Files:** none (verification only)

**Interfaces:**
- Consumes: Tasks 1–3 complete.
- Produces: green targeted suite; branch ready for PR.

- [ ] **Step 1: Run the combined affected scope**

Run: `uv run pytest tests/skills_packs/ tests/canon_fs/ tests/agents/ -q`
Expected: PASS. (Known caveat from project memory: TUI pilot tests elsewhere go red under load — they are outside this scope; do not run suites in parallel with this one.)

- [ ] **Step 2: Confirm the pack is readable end-to-end**

Run:
```bash
uv run python -c "
import asyncio
from novelizer.canon_fs.skills_route import build_skills_backend
async def main():
    b = build_skills_backend()
    r = await b.aread('/output-conventions/SKILL.md')
    assert r.error is None, r.error
    assert 'name: output-conventions' in r.file_data['content']
    print('pack readable OK')
asyncio.run(main())
"
```
Expected: `pack readable OK` (the route strips the `/skills/` prefix; backend-relative path is `/output-conventions/SKILL.md`).

- [ ] **Step 3: Commit any stragglers, push, open draft PR**

```bash
git status --short   # expect clean
git push -u origin worktree-output-conventions-skill-pack
gh pr create --draft --title "Output-conventions skill pack + tooled-builder pointer" --body "..."
```
