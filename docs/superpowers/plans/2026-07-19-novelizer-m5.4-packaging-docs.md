# M5.4 · Packaging, Docs, and the Stranger Acceptance Walkthrough — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps
> use checkbox (`- [ ]`) syntax for tracking.

> **NEVER create a `.env` file at any point in this plan.** Any scratch/temp file goes in
> the job's tmp dir (`$CLAUDE_JOB_DIR/tmp` in this environment), never the repo. Live-LLM
> settings load through `load_effective_settings()` only — never bare `EffectiveSettings()`.
> The live model in this environment is `qwen3.6-27b-mtp` via `NOVELIZER_AUTHOR_MODEL` /
> `NOVELIZER_AGENT_MODEL`, endpoint via `NOVELIZER_LLM_BASE_URL` — never hardcode the URL in
> a test or doc; rely on env. `uv tool install` steps in this plan install into an isolated
> tool environment, not the dev venv — do not let a task leave the dev environment broken.

**Branch:** `m5.4-packaging-docs` (already checked out in this worktree, based on `master` @
`76dde93` = everything through M5.3 + engine-room telemetry).

**Goal:** M5.4 carries the whole-project done-when. Per Locked decision 12, this is
"verify and harden," not "build from scratch": `pyproject.toml` already declares
`[tool.uv] package = true` and a `novelizer` console script; the setup wizard
(`novelizer/tui/setup_wizard.py`) and settings TUI already exist. This plan (1) proves the
install path works from a clean checkout with no pre-existing config, scripted and
CI-safe; (2) audits runtime dependencies for dev-only leakage; (3) rewrites `README.md`
from its stale M0-era framing to describe the real, shipped product surface; (4) adds
`docs/QUICKSTART.md` mirroring the acceptance walkthrough's steps 1–4 exactly; (5) actually
executes walkthrough steps 1–4 in this environment and records results; (6) closes out
M5.4, M5, and flips `MILESTONES.md`'s M5 row, honestly handing off steps 5–7 (the ~day-long
unattended run and human coherence judgment) to the user, per M4's honest-closeout
precedent — this plan does not claim that judgment.

**Tech Stack:** Python 3.13, `uv`, `click`, `textual`, `pytest`.

## Global constraints

- No new product features. Every task is verification, dependency hygiene, or docs.
- Every claimed command/keybinding in README/QUICKSTART must be checked against the actual
  CLI (`novelizer/director/cli.py`) or TUI code before being written down — do not describe
  aspirational surface. Each doc task below has an explicit "verified against" step.
- Full suite green (`uv run pytest`) at the end of every task that touches code or config.
- Do not run `uv tool install` steps in a way that clobbers the dev environment; do not
  leave a stray `~/.config/novelizer/config.toml` or `~/.local/share/uv/tools/novelizer`
  behind from ad hoc testing — clean up after each verification step (`uv tool uninstall
  novelizer`) so the machine's real dev environment is untouched, and use an isolated
  `XDG_CONFIG_HOME`/temp dir for any config-directory testing rather than the real
  `~/.config/novelizer`.

## Facts established during planning (do not re-derive)

- `load_effective_settings()` already tolerates a missing global config file:
  `novelizer/settings/loader.py:74` does
  `parse_global(load_toml_file(gpath), source=str(gpath)) if gpath.exists() else GlobalConfig()`
  — so a bare `EffectiveSettings`-backed command against an empty `$XDG_CONFIG_HOME` does
  **not** traceback on the settings load itself. The open question this plan verifies is
  whether the *CLI command layer* (`_resolve_story`, `_with_runtime`) behaves the same way
  end to end (e.g. does `_resolve_story`'s fallthrough to a default story directory succeed
  without a story existing yet, or does some downstream call assume one exists).
- `_resolve_story` (`novelizer/director/cli.py:57-77`) resolution order: `--story` flag →
  `base.last_opened_story` if it's a valid story dir → legacy flat-layout migration prompt
  → (unread tail of the function, presumably creates/returns a default story dir). Task 1
  reads the full function and traces this path with a debugger-free clean run.
- The CLI group's no-subcommand path (`cli()` in `cli.py:142-153`) is the *interactive*
  wizard + picker flow (`_interactive_startup`), gated on `global_config_path().exists()`.
  Subcommands (`seed`, `chapters`, `read`, `retcons`, `voices`, `voice-scaffold`,
  `autonomy`, `proposals`, `approve`, `reject`) go through the `else` path at `cli.py:154-159`,
  which calls `load_effective_settings()` unconditionally (no wizard gate) — so `novelizer
  voices` etc. against a config-free environment is a real, CI-testable "does this crash"
  question, distinct from the interactive wizard launch.
- README's current dependency/config section, autonomy levels, voice/casting flow, thread
  and secret ledger sections, and Story Brain section are all **already accurate** — this
  is not an M0-era README throughout; only the opening framing paragraph
  ("Currently at milestone **M0 (Heartbeat)**...") and the missing Story Brain views
  (Story Shape/Thread Board present; Who-Knows-What/Causeway/theme browser section,
  Structure Analyst as 7th agent, Engine Room, autonomy dial defaults) need reconciling
  against the real shipped state. Read the whole file again during Task 3 — do not assume
  this summary is exhaustive; new content may have landed since this plan was written.
- `pyproject.toml` runtime `dependencies` currently includes `pytest>=8.0.0` and
  `pytest-asyncio>=0.24.0` alongside `hypothesis>=6.156.6` — all three read as dev/test-only
  packages sitting in the runtime dependency list rather than a `[dependency-groups]` /
  `[tool.uv.dev-dependencies]` dev group. Task 2 confirms whether any runtime module
  actually imports them (grep before removing) and if not, moves them to a dev group.

---

### Task 1: Install-path verification script + CI-safe first-run smoke test

**Files:**
- Add: `scripts/verify_install.sh` (or `.py` — pick whichever reads cleaner; a bash script
  invoking `uv tool install`/`uninstall` plus `novelizer --help` etc. is simplest and needs
  no test-runner integration)
- Add: `tests/test_install_smoke.py` (CI-mechanical, no `live_llm` marker — must not call an
  LLM or crash without one)

**Problem:** Nothing today proves `uv tool install .` produces a working `novelizer` binary
independent of repo cwd, and nothing proves the CLI survives a totally empty config
directory. This is the literal first two acceptance-walkthrough steps (no prior config,
`uv tool install`, confirm `novelizer` on `PATH`; run `novelizer`, confirm no crash on
missing global config).

**Design:**
- `scripts/verify_install.sh`: in a subshell, set `XDG_CONFIG_HOME` (and any other config
  root `novelizer/settings/global_store.py`'s `global_config_path()` derives from — read
  that function first) to a fresh temp dir, `cd` to a temp dir (not the repo checkout, to
  prove no cwd dependence), `uv tool install <path-to-this-checkout>`, then run
  `novelizer --help` and confirm exit 0 and expected command names appear in output; then
  `novelizer voices` against the still-empty config and confirm it does not traceback
  (either succeeds with sensible empty-state output, or fails with a clean `click.ClickException`
  message — a *handled* failure is acceptable, an unhandled Python traceback is not); then
  `uv tool uninstall novelizer` to clean up. Script exits non-zero on any unexpected
  traceback or missing binary.
- `tests/test_install_smoke.py`: same checks but pytest-native and without shelling out to
  `uv tool install` (too slow/networked for every CI run) — instead invoke the CLI in-process
  via Click's `CliRunner` with `XDG_CONFIG_HOME`/equivalent monkeypatched to an empty
  `tmp_path`, asserting `novelizer --help`, `novelizer voices`, and `novelizer chapters`
  (or whichever subcommands read config before touching a story) exit cleanly (`result.exit_code
  == 0` or a `click.ClickException`-driven clean message, never `result.exception` being an
  unhandled traceback type) with zero pre-existing config or story directory. This is the
  regression-catching CI artifact; the shell script is the literal walkthrough-step
  reproduction, run manually/once, not part of the pytest suite.
- If any subcommand *does* traceback against empty config (e.g. `_resolve_story`'s
  fallthrough assumes a story exists, or `voices` assumes a voice pack file already resolved
  to a path that requires a story directory to exist), fix it minimally at the CLI layer —
  this is exactly the kind of first-run bug Locked decision 12 asks this sub-milestone to
  catch, and a one-line guard (e.g. create the default story dir lazily, or print a clean
  "no story yet, run `novelizer` to create one" message) is in scope even though it's "new
  code," because it's hardening an already-declared surface, not new product feature.

- [ ] **Step 1:** Read `novelizer/settings/global_store.py::global_config_path` and
      `novelizer/director/cli.py::_resolve_story` in full (the parts not already excerpted
      above) to know exactly what "empty config" needs to look like and what the fallthrough
      branch after the legacy-migration check does.
- [ ] **Step 2:** Write `tests/test_install_smoke.py` first (red), run it, observe whether it
      fails because of an actual crash or because assertions are wrong; fix the CLI if it's a
      real crash, fix the test if the assumption was wrong. Get to green.
- [ ] **Step 3:** Write `scripts/verify_install.sh`, run it manually in this environment
      (`bash scripts/verify_install.sh`), capture its output. This is allowed to take a couple
      of minutes (real `uv tool install` resolves the dependency tree from scratch).
- [ ] **Step 4:** Record in this task's completion note: exact commands run, exit codes,
      and — critically — confirm `uv tool uninstall novelizer` was run afterward so the
      environment is clean.
- [ ] **Step 5:** Full suite green (`uv run pytest`).

---

### Task 2: Dependency audit — runtime vs. dev

**Files:**
- Modify: `pyproject.toml`

**Problem:** `pytest`, `pytest-asyncio`, and `hypothesis` currently sit in `[project]
dependencies` (runtime), which means `uv tool install novelizer` pulls the entire test
toolchain onto a stranger's machine for no reason — the audit item Locked decision 12/M5.4's
row explicitly calls for.

- [ ] **Step 1:** `grep -rn "^import pytest\|^from pytest\|^import hypothesis\|^from hypothesis"
      novelizer/` (the runtime package, not `tests/`) to confirm zero runtime imports of
      these three packages. If any exist (e.g. a `pytest.mark` decorator accidentally used
      outside `tests/`), that's a separate bug to flag, not silently work around.
- [ ] **Step 2:** Move `pytest`, `pytest-asyncio`, `hypothesis` out of `[project] dependencies`
      into a `[dependency-groups]` `dev` group (uv's native mechanism — check `uv`'s current
      version behavior via `uv --version`/`uv pip --help` if unsure of exact syntax; the
      `[tool.pytest.ini_options]` block stays where it is, only the dependency declarations
      move). Check whether a `[tool.uv]` `dev-dependencies` legacy key is already used
      elsewhere in the repo (it isn't, per the `pyproject.toml` read during planning) before
      picking the syntax.
- [ ] **Step 3:** Check every remaining runtime dependency
      (`aiosqlite`/`chromadb`/`click`/`deepagents`/`httpx`/`langchain`/`langchain-openai`/
      `langgraph`/`pydantic`/`pydantic-settings`/`rich`/`textual`/`tomli-w`) is actually
      imported somewhere under `novelizer/` — a quick `grep -rl "^import <pkg>\|^from <pkg>"
      novelizer/` per package is enough; flag (don't necessarily remove) anything that comes
      back empty, since a false negative from an indirect/transitive import is possible and
      shouldn't be blindly deleted without checking `uv.lock`/actual usage more carefully.
- [ ] **Step 4:** `uv sync` (or `uv lock` then `uv sync`) to confirm the dev environment still
      resolves cleanly after the dependency-group move, then `uv run pytest` full suite green
      — this proves the dev workflow (`uv sync` + `uv run pytest`) still works with dev deps
      relocated, which is the other half of "hasn't broken anything," not just the tool-install
      path from Task 1.
- [ ] **Step 5:** Re-run the Task 1 smoke test/script if the dependency change is nontrivial,
      to confirm `uv tool install` still succeeds with a smaller runtime dependency set.

---

### Task 3: README rewrite

**Files:**
- Modify: `README.md`

**Problem:** The opening framing ("Currently at milestone **M0 (Heartbeat)**...") is stale;
the shipped product is a seven-agent room (Author, Editor, CharacterKeeper, WorldArchitect,
ContinuityChecker, Retconner, StructureAnalyst) with Story Brain views (Activity Feed, Story
Browser incl. themes section, Agent Roster, Detail Pane, Thread Board, Story Shape,
Who-Knows-What, Causeway, Engine Room) and mature autonomy/voice/casting/mining/theme
machinery through M5.3. This task brings the whole file current, not just the opening line —
read it in full again (it may have already-accurate sections from prior milestones; only
change what's actually stale) and cross-check every command/keybinding against code.

- [ ] **Step 1:** Read the current `README.md` in full (again, fresh, in this task — do not
      rely solely on the excerpt captured during planning; other work may have touched it
      since, per the teammate brief's note that an external character-keeper commit recently
      touched it).
- [ ] **Step 2:** Read `docs/superpowers/specs/2026-07-17-novelizer-vision-design.md` for the
      "director's control room for an autonomous writers' room" framing language and the
      canonical list of Story Brain views/event domains, to source the rewritten opening
      paragraph and product-description section from the vision doc's own words rather than
      inventing new framing.
- [ ] **Step 3:** Enumerate the real CLI surface from `novelizer/director/cli.py`: every
      `@cli.command()` (`seed`, `chapters`, `read`, `retcons`, `voices`, `voice-scaffold`,
      `autonomy`, `proposals`, `approve`, `reject`) plus the bare `novelizer` no-arg wizard/
      picker/TUI launch path. Confirm each one's exact argument signature by reading its
      `@click.argument`/`@click.option` decorators, not by memory.
- [ ] **Step 4:** Enumerate the real TUI keybindings/panes from `novelizer/tui/app.py` (or
      wherever bindings are declared — grep for `BINDINGS` or `on_key`) and the Engine Room
      (`e` key), Room drill-in (`r` key), settings (`:settings`), command palette commands
      (`seed`, `focus`, `pause`, `resume`, `autonomy`, `approve`, `reject`) — confirm each
      against the actual widget/binding code, not the current README's claims (some may
      already be accurate; verify rather than assume either way).
- [ ] **Step 5:** Enumerate Story Brain views actually present: grep
      `novelizer/tui/widgets/` for the view files (Thread Board, Story Shape, Who-Knows-What,
      Causeway, Story Browser's themes section per M5.2/`browser_model.py`'s
      `browser_sections()`) and confirm each has a real, working widget class, not a planned
      one.
- [ ] **Step 6:** Rewrite `README.md` sections in place: opening paragraph (what Novelizer is,
      current milestone state — "M0–M5 shipped" or equivalent, not a specific in-progress
      milestone number since M5.4 closes the project), Installation (`uv tool install .` /
      `uv tool install novelizer` once published, alongside the existing `uv sync` dev-setup
      note — keep both, labeled for their different audiences: end-user install vs.
      contributor dev setup), first-run (wizard → endpoint → models → save, matching the
      Configuration section's existing accurate description — verify it's still accurate
      against `setup_wizard.py`), casting (voice-scaffold + settings flow — confirm the
      existing "Voices" section's `voice-scaffold` example still matches the CLI signature
      read in Step 3, and add the settings-flow half if missing: how a director actually
      switches the active pack/profile via `:settings`, not only via env var, since M5.4's
      walkthrough step 3 requires this without hand-editing TOML), seeding (existing section,
      verify), Story Brain views section (new or expanded — list all views with a one-line
      description each, sourced from Step 5's confirmed list), Engine Room (verify existing
      section against `e`/`p` keybindings in code), autonomy dial + approval queue (existing
      section, verify against `commands.py`/`autonomy` CLI signature).
- [ ] **Step 7:** For every command/keybinding claim in the rewritten README, add nothing
      that wasn't confirmed in Steps 3–5 — if something looks plausible but wasn't traced to
      code, either trace it before writing it down or omit it.
- [ ] **Step 8:** No test to run (docs-only), but do a final read-through diffing old vs. new
      README for anything accidentally deleted that was still accurate (e.g. the thread
      ledger / secret & causal-edge ledger / character voices sections read as accurate in
      the planning excerpt — preserve and lightly update these rather than rewriting from
      scratch, since rewriting risks introducing new inaccuracies where the existing prose
      was fine).

---

### Task 4: `docs/QUICKSTART.md` — the exact stranger path

**Files:**
- Add: `docs/QUICKSTART.md`

**Problem:** The acceptance walkthrough in `docs/submilestones/M5-finish.md` (lines 34-58)
is the done-when for the whole project; a stranger needs a copy-pasteable version of it, not
prose scattered across the README.

**Design:** Mirror the walkthrough's 7 steps as sections:
1. Install (`uv tool install .` or `uv tool install novelizer`) + PATH confirmation —
   exact command from Task 1's verification, plus expected output.
2. First run (`novelizer`) — wizard screen description (what you'll see, what to enter:
   endpoint URL, test-connection button, model picker, save) sourced from
   `setup_wizard.py`'s actual widget IDs/labels (Step 2 of Task 3's CLI/TUI enumeration
   already gathers this — reuse it) — no hand-waving ("configure it") — literal field names.
3. Casting: exact `novelizer voice-scaffold <name> "<description>" --pack <path>` command
   (already in README, reuse verified form) + exact settings-flow steps to activate it
   (`:settings` screen field names) + restart note. Seeding: exact `novelizer seed "..."` or
   in-TUI command-palette `seed <text>`.
4. Autonomy: exact `novelizer autonomy <level>` command, list the four levels with one-line
   meaning each (reuse README's existing accurate table).
5. What to expect during an unattended run: where chapters/threads/themes/retcons appear
   (Activity Feed, Story Browser, Thread Board, Story Shape, Who-Knows-What, Causeway,
   approval queue pane) — descriptive, not a command, since this step is about observing the
   TUI live.
6. "Leave it running about a day" — set expectations on run duration/inference load, note
   `max_concurrent_agents` setting from M5.3 governs saturation.
7. Judging coherence — the human-judgment criteria verbatim from the walkthrough (consistent
   voices, no unresolved leaks presented as fine, threads/themes touched or explicitly left
   open) as a checklist the reader applies themselves.

- [ ] **Step 1:** Write `docs/QUICKSTART.md` following the design above, cross-checking every
      command against the same code reads Task 3 performed (do Task 3 first if not already
      done, so QUICKSTART can reuse its verified command list rather than re-deriving it).
- [ ] **Step 2:** For each of the 4 scriptable steps (install, first-run structure, casting
      commands, autonomy command), add an inline note: "verified against: `<file>:<line>` /
      `scripts/verify_install.sh` output from Task 1" — a lightweight provenance trail so a
      future reader can tell this wasn't guessed.
- [ ] **Step 3:** No automated test (it's a doc), but this task's completion note must state
      which of the 7 steps were *actually executed* in this environment (feeding into Task 5)
      versus described from code-reading alone.

---

### Task 5: Execute acceptance-walkthrough steps 1–4 in this environment

**Files:** none modified (verification run); results recorded in Task 6's closeout.

**Problem:** Locked decision 12 and the M5.4 done-when require steps 1–4 to actually run,
not just be documented as runnable. This task performs the run.

**Design:**
- Use an isolated `XDG_CONFIG_HOME` (temp dir under `$CLAUDE_JOB_DIR/tmp`, never the real
  `~/.config/novelizer`) and a fresh story directory, so this doesn't collide with the user's
  own Novelizer config or any other parallel job's.
- Step 1: run `scripts/verify_install.sh` from Task 1 (or repeat its steps manually if the
  script needs adjustment) — confirms `uv tool install .` + PATH.
- Step 2: the wizard is interactive (`SetupWizardApp`, a Textual app) — this plan does not
  attempt to drive a Textual TUI headlessly for OAuth-style interactive input. Instead,
  write the global config directly via the same `write_global_config` function the wizard
  calls (`novelizer/settings/global_store.py`), pointing at the live endpoint via
  `NOVELIZER_LLM_BASE_URL`/model env vars already established in this environment, and treat
  this as the one explicitly-manual step, documented as such in the closeout — per the
  teammate brief's own framing ("decide how to verify it non-interactively or document it as
  the one manual step"). Confirm afterward that `novelizer voices`/`novelizer chapters`
  against this config do not crash (reusing Task 1's smoke pattern), which is the part of
  step 2 that *is* mechanically verifiable (no crash on a populated-but-fresh config).
- Step 3: run `novelizer voice-scaffold <name> "<description>" --pack <temp-pack-path>` for
  at least one profile, confirm the TOML file is written; set it active via the same
  mechanism the settings screen would write (either drive the settings TUI screen
  programmatically if it has a testable API, or write the equivalent story/global config
  field directly and note which approach was used). Seed a new world: `novelizer seed "..."`
  or the `create_story`/CLI seed path — confirm a story directory with `world.db` exists
  afterward.
- Step 4: `novelizer autonomy <level>` above the most conservative
  (confirm exact level names/ordering from the README's autonomy table — "most conservative"
  is `gated_all` per the table, so anything above that, e.g. `gated_canon`, `gated_retcons`,
  or `full_auto`, satisfies this). Start the room briefly against the live endpoint
  (`NOVELIZER_AUTHOR_MODEL`/`NOVELIZER_AGENT_MODEL=qwen3.6-27b-mtp`) — a short, bounded run
  (a few minutes, not the full day), enough to confirm agents actually dispatch and at least
  one canon event commits, then stop it cleanly (Ctrl-C equivalent / process termination,
  not a kill -9 that could corrupt the SQLite log — confirm `world.db` is still readable
  afterward via `novelizer chapters`).
- [ ] **Step 1:** Execute the four steps above in sequence, in an isolated temp environment,
      capturing command + output for each.
- [ ] **Step 2:** After the brief live run, confirm the story's `world.db` is intact
      (`novelizer chapters` succeeds, lists at least whatever committed during the run) and
      note whether any Story Brain view data (threads/themes/retcons) appeared, even in a
      short window — this is a bonus observation, not a requirement (the full step 5 of the
      walkthrough, "chapters accumulate... at least one retcon request," is explicitly a
      longer-run outcome per step 6's "about a day," so a short verification run may
      legitimately show nothing yet — record honestly, don't force it).
- [ ] **Step 3:** Clean up: stop any running process, note whether the temp config/story dirs
      were left in place for the user's own longer run (recommended — see Task 6) or deleted.
- [ ] **Step 4:** Write down, verbatim, what was mechanically verified vs. what required the
      manual-config-write workaround for the wizard, for Task 6's closeout to quote directly.

---

### Task 6: Closeout — M5.4 row, M5 milestone closeout note, MILESTONES.md flip

**Files:**
- Modify: `docs/submilestones/M5-finish.md` (M5.4 row status + closeout note, following the
  exact format of the M5.1/M5.2/M5.3 closeout notes already in that file's table)
- Modify: `docs/MILESTONES.md` (M5 row — read its current format first; flip to complete)

**Problem:** M5.4's row is currently "not started"; the milestone needs a closeout note
recording what was mechanically verified (steps 1–4) and an honest handoff of steps 5–7 to
the user, per M4's honest-closeout precedent (M4 closed with a documented known gap rather
than a false completion claim — M5.4 does the same for the unattended-run/human-judgment
steps, which are inherently not something this plan can perform).

- [ ] **Step 1:** Read the M4 closeout note in `docs/submilestones/M4-*.md` (or wherever it
      lives — grep for it) for the exact tone/structure precedent to match: what got proven,
      what didn't, and why, without hedging or overclaiming.
- [ ] **Step 2:** Read the current `docs/MILESTONES.md` M5 row and surrounding rows for exact
      formatting (status column values, closeout-note conventions used by M1–M4's rows).
- [ ] **Step 3:** Write the M5.4 row status update and closeout note in
      `docs/submilestones/M5-finish.md`, citing: Task 1's install-smoke results, Task 2's
      dependency-audit outcome (what moved, what stayed), Task 3/4's README/QUICKSTART
      landing, Task 5's literal walkthrough-steps-1–4 execution results (including the
      wizard's manual-verification-workaround honestly named as such), and an explicit,
      unhedged statement that steps 5–7 (leave running ~a day, read the output, judge
      coherence) were **not** performed by this plan and remain the user's to run — with a
      pointer to `docs/QUICKSTART.md` as the exact path to follow, and (if Task 5 left a
      seeded, autonomy-set story running or ready) a note on where that story lives so the
      user can pick it up directly rather than re-seeding from scratch.
- [ ] **Step 4:** Flip `docs/MILESTONES.md`'s M5 row to reflect steps 1–4 verified, with the
      same 5–7 handoff caveat linked to the M5-finish.md closeout note rather than duplicated
      in full.
- [ ] **Step 5:** Full suite green (`uv run pytest`) one final time on the whole branch.
- [ ] **Step 6:** Commit. Do not merge/push without the user's go-ahead per this session's
      standing instructions — hand off with a clear PR-ready branch state.

---

## Task sizing note

Tasks 1–2 are code+test (fresh-subagent-sized, ~half a day each). Tasks 3–4 are docs-only but
require careful code cross-referencing (similarly sized). Task 5 is a hands-on verification
run best done by the same agent/session that just wrote Tasks 1–4 (it depends on their
outputs directly) rather than a fresh subagent with no context on what was already verified.
Task 6 is a short closeout, best done last by whichever agent has the fullest picture of
what actually happened across Tasks 1–5.
