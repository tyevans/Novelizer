# M0 — Audit & Seams Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce a single exhaustive seam-map document that pins, function-by-function, which parts of Novelizer's canon/agent/projection code are already domain-generic, which need a generalization pass before extraction, and which stay fiction-only — the input M1 needs to start moving code into a new substrate repo.

**Architecture:** This is a documentation-only milestone (per spec's M0 section — "No code changes — output is documentation only"). Four read-only audit passes over disjoint subsystems (canon core, projections, agent registry/autonomy, skills/tooling), each producing one section of `docs/superpowers/specs/2026-07-21-m0-seam-map.md`, then one assembly+consistency task.

**Tech Stack:** Python 3 (read-only inspection), no runtime dependencies. No test framework needed — this milestone has no code, so "testing" means the self-review checklist in Task 5, not pytest.

## Global Constraints

- Output is documentation only — no source files under `novelizer/` may be modified in this milestone (per spec's M0 scope line, verbatim: "No code changes — output is documentation only").
- The seam map must classify every item into exactly one of three buckets: **generic-as-is** (moves to the new repo unchanged), **needs-generalization** (moves, but must first stop referencing fiction-specific names/types), **fiction-only** (stays in Novelizer, is not part of the substrate).
- Every classified item must cite its file path and line range (or "whole file" for small files) — no unqualified claims like "the event store is generic."
- Do not re-litigate the spec's already-settled findings (per-event-type dial, two projection examples, six tool categories) — this plan produces the exhaustive superset the spec's "Findings" section calls a superset of, not a rehash of it.

---

### Task 1: Audit canon core (event store, autonomy, policy)

**Files:**
- Read: `novelizer/canon/event_store.py`, `novelizer/canon/events.py`, `novelizer/canon/autonomy.py`, `novelizer/canon/policy.py`, `novelizer/canon/committer.py`
- Create: `docs/superpowers/specs/2026-07-21-m0-seam-map.md` (Task 1 writes the `## Canon Core` section only; later tasks append their own sections)

**Interfaces:**
- Consumes: nothing (first task)
- Produces: a `## Canon Core` markdown section in the seam-map doc, containing one subsection per file read, each subsection a bullet list of `- \`ClassOrFunctionName\` (path:lines) — bucket — one-sentence reason`.

- [ ] **Step 1: Read the five canon-core files listed above in full.**

- [ ] **Step 2: For each public class/function in those files, classify it into generic-as-is / needs-generalization / fiction-only.**

  Use this test: does the class/function reference a fiction-specific
  `EventType` constant, a fiction-specific Pydantic payload model (e.g.
  `BlueprintAdopted`), or a fiction-specific table/column name by literal
  string? If yes → needs-generalization (name the specific reference) or
  fiction-only (if the whole item's *purpose* is fiction-specific, not just
  a parameter). If a class/function operates purely on `event_type: str`,
  `payload: dict`, `seq: int`, `stream_id: str` or similarly domain-neutral
  shapes → generic-as-is.

  Known starting points to get right (do not skip these, they anchor the
  rest of the classification):
  - `canon/policy.py`'s `_RETCON_EVENTS`, `_CANON_EVENTS`, `_ALWAYS_GATED`,
    `_NEVER_GATED` module-level sets → **needs-generalization**: they are
    literal Python sets of fiction `EventType` constants. `AutonomyPolicy.is_gated()`
    itself (the method body, not the sets) → **generic-as-is**: it only
    calls `state.level_for(agent_name)` and does set membership checks: the
    *mechanism* takes no fiction-specific parameter.
  - `canon/autonomy.py`'s `AutonomyLevel`, `AutonomyState`, `Proposal`,
    `ProposalStatus` → **generic-as-is**: no fiction-specific fields.
  - `canon/events.py`'s `EventType` class (the ~60+ constants) →
    **fiction-only**: every member name is a fiction concept
    (`BLUEPRINT_ADOPTED`, `THREAD_PLANTED`, etc). The *class shape*
    (string constants used as event-type keys) is what a domain's own
    event-type registry replicates, but the constants themselves don't move.
  - Every Pydantic payload model in `canon/events.py` (e.g.
    `BlueprintAdopted`, `BeatSpec`) → **fiction-only**, same reasoning.
  - `canon/event_store.py`'s core append/read functions (name them
    explicitly after reading — do not guess signatures) → expected
    **generic-as-is** if they operate on `(stream_id, event_type, payload,
    parent_ids, actor)` tuples without importing `EventType` or any fiction
    payload model; confirm this by checking their actual imports, don't
    assume.

- [ ] **Step 3: Write the `## Canon Core` section to the seam-map doc** with one subsection per file, following the bullet format in Interfaces above.

- [ ] **Step 4: Commit.**

```bash
git add docs/superpowers/specs/2026-07-21-m0-seam-map.md
git commit -m "docs(m0): audit canon core for substrate seam map"
```

---

### Task 2: Audit projections (canon_fs and knowledge graph)

**Files:**
- Read: `novelizer/canon_fs/backend.py`, `novelizer/canon_fs/render.py`, `novelizer/canon_fs/outline_render.py`, `novelizer/canon_fs/paths.py`, `novelizer/canon_fs/search.py`, `novelizer/canon_fs/skills_route.py`, `novelizer/store/kg_store.py`, `novelizer/store/kg_projector.py`, `novelizer/store/kg_structured.py`
- Modify: `docs/superpowers/specs/2026-07-21-m0-seam-map.md` (append `## Projections` section — this task runs after Task 1's commit exists, so append rather than overwrite)

**Interfaces:**
- Consumes: the seam-map doc file created by Task 1 (append to it, do not recreate)
- Produces: a `## Projections` section, same bullet format as Task 1, plus a short **comparison subsection** explicitly mapping `canon_fs` concepts to `kg_store`/`kg_projector` concepts (e.g. "canon_fs's per-request `_Snapshot` render ≈ kg_store's per-`event_fingerprint` mention tracking — both are 'recompute this view fresh from canon, keyed to what triggered it'").

- [ ] **Step 1: Read all nine files listed above in full.**

- [ ] **Step 2: Classify each public class/function** using the same three-bucket test as Task 1. Expected anchors:
  - `canon_fs/backend.py`'s `CanonBackend` class → mixed: the `BackendProtocol` implementation (methods like read/write/glob dispatch) is **generic-as-is** if it delegates to injected render functions rather than importing fiction render functions directly; the `KIND_DIRS = ("chapters", "characters", "world", "threads", "secrets", "themes")` constant and any direct import of `render_chapter`/`render_character`/etc. → **needs-generalization** (the dir-kind list and render-function set must become domain-supplied, not hardcoded).
  - `canon_fs/render.py`'s individual `render_*` functions → **fiction-only** (each renders one fiction entity type), but their *shape* (a function that takes a canon snapshot and entity id, returns rendered text) is the pattern a domain's own render functions replicate.
  - `store/kg_store.py`'s `KGStore` class methods (`upsert_entity`, `upsert_relation`, `link_mention`, `clear_mentions_for_fingerprint`, etc.) → **generic-as-is**: verify by checking whether any method references fiction-specific entity/relation type strings as literals (vs. accepting `entity_type: str` as a parameter) — confirm from the actual code, don't assume based on this summary.
  - `store/kg_projector.py` → read and classify; this file was not opened in the spec-writing audit, do not skip it.

- [ ] **Step 3: Write the comparison subsection** describing the shared shape between the two projection implementations, citing specific method/function names from both sides (not a restatement of the spec's prose finding — this must name the actual code on each side).

- [ ] **Step 4: Append both sections to the seam-map doc.**

- [ ] **Step 5: Commit.**

```bash
git add docs/superpowers/specs/2026-07-21-m0-seam-map.md
git commit -m "docs(m0): audit projection layer (canon_fs + kg store) for seam map"
```

---

### Task 3: Audit agent registry, middleware, and skills packs

**Files:**
- Read: `novelizer/agents/registry.py`, `novelizer/agents/registry_types.py`, `novelizer/agents/middleware.py`, `novelizer/skills_packs/__init__.py` and one representative skill pack directory (`novelizer/skills_packs/outlining/`)
- Modify: `docs/superpowers/specs/2026-07-21-m0-seam-map.md` (append `## Agent Registry, Middleware & Skills` section)

**Interfaces:**
- Consumes: the seam-map doc from Tasks 1-2 (append)
- Produces: a `## Agent Registry, Middleware & Skills` section, same bullet format.

- [ ] **Step 1: Read all files listed above.**

- [ ] **Step 2: Classify.** Expected anchors:
  - `registry_types.py`'s `AgentSpec` dataclass → **generic-as-is**: check that its fields (`name`, `agent_class`, `build_runner`, `tool_grant`, `interval_setting`, `extra_kwargs`, `fallback_name`) are all domain-neutral types (str, type, Callable) with no fiction-specific field. Confirm from the actual current file — it may have grown fields since `2026-07-20-agent-registry-design.md` was written.
  - `registry.py`'s `AGENT_REGISTRY` list itself → **fiction-only**: it's the literal list of fiction agents (`world_architect.SPEC`, `author.SPEC`, etc). The *pattern* (a module-level list of `AgentSpec` in scheduling order) is what a domain's own registry replicates.
  - `agents/middleware.py`'s `CompositeBackend` wiring → classify the specific wiring code (does it hardcode `/canon` and `/scratch` path prefixes as fiction-specific, or are those just convention names any domain could reuse verbatim?). State which.
  - `skills_packs/__init__.py` and the `outlining` pack → classify the *loader mechanism* (generic-as-is if it just walks a directory of `SKILL.md`-shaped files) separately from the *pack content* (fiction-only, since outlining/promise-payoff/etc. are fiction craft skills).

- [ ] **Step 3: Append the section.**

- [ ] **Step 4: Commit.**

```bash
git add docs/superpowers/specs/2026-07-21-m0-seam-map.md
git commit -m "docs(m0): audit agent registry, middleware, and skills packs for seam map"
```

---

### Task 4: Audit deepagents integration points

**Files:**
- Read: `novelizer/runtime.py` (focus on lines referencing `deepagents`, especially the `CompositeBackend`/`StateBackend` construction noted around lines 111-116 in the earlier audit — re-locate exact current line numbers, they may have shifted)
- Read: installed `deepagents` package's `backends/protocol.py` and `backends/utils.py` (already imported in `canon_fs/backend.py` — use that import to locate the installed package path, e.g. via `python -c "import deepagents, os; print(os.path.dirname(deepagents.__file__))"`)
- Modify: `docs/superpowers/specs/2026-07-21-m0-seam-map.md` (append `## deepagents Integration` section)

**Interfaces:**
- Consumes: the seam-map doc from Tasks 1-3 (append)
- Produces: a `## deepagents Integration` section listing exactly which deepagents primitives Novelizer depends on (class/function names + version, from `pyproject.toml`'s `deepagents>=0.6.12` pin) and confirming (or correcting) the spec's claim that deepagents supplies the agent loop, sub-agent delegation, and `BackendProtocol`/`CompositeBackend`, but not event-sourcing.

- [ ] **Step 1: Locate deepagents' installed source** via the command above and read `backends/protocol.py` and `backends/utils.py`.

- [ ] **Step 2: Read `novelizer/runtime.py`** in full, noting every `deepagents` import and how each imported name is used.

- [ ] **Step 3: Write the section**, structured as: (a) list of deepagents classes/functions Novelizer imports, one line each with what it's used for; (b) one paragraph confirming or correcting the spec's "deepagents is not event-sourced" claim against what you actually read in `backends/protocol.py` (does `BackendProtocol` or any shipped backend implement anything append-only/event-sourced? If yes, correct the spec's claim explicitly rather than silently agreeing with it).

- [ ] **Step 4: Commit.**

```bash
git add docs/superpowers/specs/2026-07-21-m0-seam-map.md
git commit -m "docs(m0): audit deepagents integration points for seam map"
```

---

### Task 5: Assemble, cross-check, and finalize the seam map

**Files:**
- Modify: `docs/superpowers/specs/2026-07-21-m0-seam-map.md` (add a header and a summary table; this is the only task that edits earlier sections rather than only appending)

**Interfaces:**
- Consumes: all four sections written by Tasks 1-4
- Produces: the finalized seam-map document, ready for M1 to consume as its literal work list.

- [ ] **Step 1: Read the full assembled document** (all four sections from Tasks 1-4).

- [ ] **Step 2: Add a document header above `## Canon Core`:**

```markdown
# M0 Seam Map — Substrate Extraction Candidates

Status: complete. Input to M1 (extract the substrate skeleton).

This document classifies every audited class/function into exactly one
bucket: **generic-as-is** (moves to the new substrate repo unchanged),
**needs-generalization** (moves, but must stop referencing fiction-specific
names/types first — the required change is stated inline), or
**fiction-only** (stays in Novelizer).
```

- [ ] **Step 3: Add a summary table immediately after the header**, one row per item across all four sections, columns: `Item | File:Lines | Bucket | Note`. Build this by mechanically walking the four sections you wrote in Tasks 1-4 — every bullet becomes one row. Do not summarize or drop items; the table must have the same item count as the four sections combined.

- [ ] **Step 4: Cross-check for consistency.** Confirm no item appears in two different buckets across sections (e.g. `CanonBackend` should not be called generic-as-is in one place and fiction-only in another). Fix any contradiction found by re-reading the relevant file and picking the correct bucket.

- [ ] **Step 5: Confirm every "needs-generalization" item has a stated required change**, not just a bucket label. Any that don't, add one by re-reading that item's file.

- [ ] **Step 6: Commit.**

```bash
git add docs/superpowers/specs/2026-07-21-m0-seam-map.md
git commit -m "docs(m0): finalize seam map with summary table and consistency pass"
```

- [ ] **Step 7: Merge to main.** No remote is configured for this repo, so "merge" is a local git operation only.

```bash
git checkout main
git merge worktree-zany-bubbling-stallman --no-edit
```

Expected: fast-forward or clean merge (this milestone touches only new
documentation files, no source under `novelizer/`, so conflicts are not
expected). If a conflict occurs, stop and report it rather than resolving
destructively.

- [ ] **Step 8: Confirm on main.**

```bash
git log --oneline -6
git status --short
```

Expected: the six M0 commits appear in `main`'s log, working tree is clean.
