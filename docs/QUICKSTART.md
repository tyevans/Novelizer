<!--
verified against (2026-07-19): scripts/verify_install.sh (Task 1's install-path verification,
its actual output copied below), novelizer/director/cli.py (command signatures),
novelizer/tui/setup_wizard.py (widget ids), novelizer/tui/settings_screen.py,
novelizer/settings/view_model.py (RESTART_REQUIRED_KEYS), README.md's Autonomy Levels
table, novelizer/tui/app.py (BINDINGS), novelizer/tui/widgets/ (Story Brain view classes),
docs/submilestones/M5-finish.md (the M5.4 acceptance walkthrough this file mirrors,
lines 34-58 in that file's numbering at time of writing)
-->

# Quickstart: install to first run

> **New to Novelizer?** Start with the step-by-step tutorial instead:
> [`docs/tutorial/first-story.md`](tutorial/first-story.md). It covers the same
> install-to-first-chapter path in a guided, learning-oriented form. This file remains as
> the condensed acceptance walkthrough (autonomy dial, canon tools, promise ledger, and
> the day-long-run judgment steps the tutorial doesn't cover).

This is the exact path a stranger — someone with no prior Novelizer config, on a machine
that already has a reachable OpenAI-compatible LLM endpoint — follows to get the room
running. It mirrors the M5.4 acceptance walkthrough in
[`docs/submilestones/M5-finish.md`](submilestones/M5-finish.md) step for step. Steps 1-4
are copy-pasteable commands; steps 5-7 describe what you'll see and do once the room is
running unattended — you perform those yourself, they aren't scripted here.

## 1. Install

From a checkout of this repository (there is no published PyPI package yet, so
`uv tool install novelizer` without a path does not work):

```bash
uv tool install .
```

This installs an isolated `novelizer` binary onto your `PATH`, independent of your current
working directory. Confirm it worked:

```bash
novelizer --help
```

You should see the full command list (`seed`, `chapters`, `read`, `retcons`, `voices`,
`voice-scaffold`, `autonomy`, `proposals`, `approve`, `reject`) and exit code 0.
*Verified against: `scripts/verify_install.sh`, which performs this exact install/uninstall
cycle in an isolated `UV_TOOL_DIR` and asserts on the `--help` output — see that script for
the CI-safe reproduction.*

## 2. First run — the setup wizard

With no `~/.config/novelizer/config.toml` yet, just run:

```bash
novelizer
```

This opens the setup wizard (no crash on missing config — the wizard *is* the first-run
path). Every field has a visible label with a dim help line beneath it explaining what
you're setting. You'll see:

- **LLM base URL** — a text field pre-filled with `http://localhost:8080/v1`; point it at
  your OpenAI-compatible endpoint (llama.cpp, vLLM, Ollama, LM Studio, OpenRouter…),
  including the `/v1` suffix.
- **API key** — a password field, sent as a Bearer token; leave blank for local endpoints
  that don't require one.
- **Stories directory** — pre-filled with `stories`; where your story directories will
  live. `~` expands; relative paths resolve from where you launch novelizer.
- **Test connection** — a button; click it to probe the endpoint and populate the model
  pickers below from its live model list.
- **Author model** / **Agent model** / **Embedding model** — three dropdowns, disabled
  (showing "run Test connection first") until the connection test succeeds. Author writes
  the prose (pick your strongest model), Agent runs the support agents (a faster model
  works well), Embedding builds the semantic index for canon search.
- **Save & continue** — writes your global config and proceeds to the story picker (only
  enabled once you've saved or the connection test has populated the model lists).
- **Skip model picks — save endpoint only** — an escape hatch if you just want the endpoint
  saved without picking models yet.

*Verified against: `novelizer/tui/setup_wizard.py`'s actual widget ids
(`base_url`, `api_key`, `stories_dir`, `probe`, `author_model`, `agent_model`,
`embed_model`, `save`, `skip`) and its `_field` label/help copy.*

After the wizard, a story picker appears — it lists any existing stories in your stories
directory (empty on a first run) and lets you create a new one. `novelizer --story
path/to/story/` skips the picker entirely if you already know where you're going.

## 3. Casting the room and seeding a world

Novelizer has no in-TUI voice/personality editor by design — casting is CLI-scaffold plus
settings-screen activation, not hand-edited TOML.

**Scaffold a new prose profile** (no LLM call — your description becomes the profile's
casting note verbatim):

```bash
novelizer voice-scaffold brisk "Fast, punchy, present-tense action prose." --pack stories/user_pack.toml
```

**Activate it**: inside the TUI, focus the command input with `Ctrl+K` and type `settings`
(or just `settings` — the leading `:` shown in the status bar is cosmetic). In the settings
screen, select the `voice_pack` row, press Enter, type `stories/user_pack.toml`, and submit;
repeat for the `prose_profile` row with `brisk`. Both apply live — no restart needed
(`voice_pack`/`prose_profile` are not in the settings screen's restart-required set, unlike
endpoint/model changes). Press `Escape` to leave the settings screen.

**Seed a new world** — a narrative seed the Author picks up on its next pass:

```bash
novelizer seed "A stranger arrives at the gates at dusk."
```

or, from inside the TUI's command input: `seed A stranger arrives at the gates at dusk.`

*Verified against: `novelizer/director/cli.py`'s `voice_scaffold`/`seed` command
signatures, `novelizer/tui/settings_screen.py` (row-select-then-edit flow,
`RESTART_REQUIRED_KEYS` in `novelizer/settings/view_model.py` confirming voice_pack/
prose_profile are excluded), `novelizer/tui/app.py`'s command dispatch.*

## 4. Set the autonomy dial

Autonomy levels, most to least conservative:

| Level | Meaning |
|---|---|
| `gated_all` | All agent canon-changing events queue as proposals (rare; for testing) |
| `gated_canon` | All agents' canon events queue as proposals; director signals (seed/focus/pause/resume) always auto-append |
| `gated_retcons` | Only Retconner output queues as proposals; other agents auto-append |
| `full_auto` | All agents' events append immediately — no approval queue (default) |

Set it above the most conservative level (`gated_all`) to let the room run unattended:

```bash
novelizer autonomy full_auto
```

or, from the TUI command input: `autonomy full_auto` (optionally with a per-agent override,
e.g. `autonomy gated_retcons editor` — the agent id is lowercase; a display-cased name like
`Editor` creates a silently-ineffective override).

*Verified against: `novelizer/director/cli.py`'s `autonomy` command and the level names in
`novelizer/canon/autonomy.py`'s `AutonomyLevel`.*

## 5. What to expect while it runs

Launch (or return to) the TUI with `novelizer`. As the room works, watch:

- **Activity Feed** (left pane, top) — chapters drafted, retcons filed, and in-personality
  remarks from the agents, live.
- **Story Browser** (right pane) — chapters, characters, world entries, retcons, and themes
  accumulating; click any item for its detail.
- **Thread Board / Story Shape / Who-Knows-What / Causeway** — the Story Brain views,
  stacked below the feed — show plot threads and their staleness, chapter tension/pacing,
  who knows which secrets, and declared cause-effect links, all derived live from the same
  canon.
- **Approval queue** — if autonomy is anything but `full_auto`, pending proposals appear
  here for `approve`/`reject`.
- **Engine Room** (`e` to toggle) — the live machinery view: which agent is running, tokens
  streaming in, call vitals, and a durable trace of every run and scheduler decision.

Over a longer run, expect at least one retcon request to get auto-filed from some source
(an LLM-noticed contradiction, a leak, a paradox, a mined fact, or a voice-drift flag) —
that's the reliability path (prose mining backfilling undeclared facts) doing its job, not
a bug.

## 6. Leave it running

Per the walkthrough this file mirrors, leave the room running roughly a day for chapters,
threads, and themes to accumulate into something novella-length. How fast that happens
depends on your endpoint's throughput and the `max_concurrent_agents` setting (default 2 —
how many agents the scheduler dispatches concurrently each tick); raise it if your endpoint
can sustain more parallel inference load, lower it if the room saturates the endpoint.

## 7. Judge the result

Come back and read the accumulated chapters as prose (`novelizer chapters` to list, then
`novelizer read <chapter-id>`, or the TUI's Reading mode — `v` to toggle), not as a log
dump. Ask:

- Are character voices consistent across chapters?
- Are there unresolved leaks presented as if they were fine (check Who-Knows-What and the
  approval queue for any still-open retcons)?
- Were threads and themes that got planted eventually touched or paid off — or, if left
  open, is that a deliberate authorial choice rather than something silently forgotten
  (check the Thread Board for `STALE` markers)?

There's no automated oracle for this judgment — it's the one step in this walkthrough that
requires you to actually read the book.

## 8. Canon tools (pull agents)

Every LLM agent in the room — the Author, Continuity Checker, chat personas, and the five
phase-b agents (World Architect, Character Keeper, Editor, Retconner, Structure Analyst) —
runs with *canon pull tools* by default: a read-only virtual filesystem over the story
record (`ls`, `read_file`, `grep`, `glob` over `/chapters`, `/characters`, `/world`,
`/threads`, `/secrets`, `/themes`) plus semantic `search_canon`. Watch the Engine Room for
`⚒` lines to see an agent researching canon before it writes.

Each agent has a settings flag (global, story, or `NOVELIZER_*` env, like any setting):
`author_tools_enabled`, `checker_tools_enabled`, `chat_tools_enabled`,
`world_architect_tools_enabled`, `character_keeper_tools_enabled`, `editor_tools_enabled`,
`retconner_tools_enabled`, `structure_analyst_tools_enabled` — all default `true`.

Turning one off reverts that agent to its legacy push-only prompt (no canon tools, and
pushed context reverts from index form — chapter maps, lore-title and cast name lists,
entry-id listings, the Editor's cast pointer — back to inline prose excerpts, entry
bodies, traits, and voice cards). Use
this if a small local model handles tool-calling poorly. Flag changes take effect on
restart — mid-session edits are deliberately inert until then.

## 9. Promise ledger & resolution windows

The room keeps a ledger of *promises* — discrete planted setups (a Chekhov's gun, a
foreshadowed image, a deliberate red herring) that owe the reader a payoff. The Author
and Editor declare them as they write and review; nothing is required from you. Open
promises appear in a **Ledger** section on the Threads tab (`2`), with `◇` markers and
window badges; overdue ones surface in the alarm strip.

You can set target resolution windows yourself (until the Plotter agent arrives in M8):

```bash
novelizer plan-resolution THREAD_ID 18 20 --note "pay it off at the gate"
novelizer plan-reveal SECRET_ID 5 9
```

Windows are 1-based chapter numbers (`lo hi`; `0 0` clears). Threads past their window
show `OVERDUE` on the board and raise a "Resolution pacing" note; overdue promises raise
the "Promise ledger" note nudging agents to pay or release. Three-plus resolutions
targeting the same span raise a congestion warning before it happens.

## 10. The Plotter and the blueprint

The room now has a ninth agent: the **Plotter**, its showrunner. It never writes prose.
When a story has chapters but no adopted shape, it proposes a **blueprint** — a structural
framework (e.g. the six-position map), a target length, and minted beats with target
chapter windows. Blueprint adoption is special: it lands in your **approval queue at every
autonomy level**, even full auto — adopting a shape re-frames the whole book, so the
Director always signs off.

Once a blueprint is active, the Plotter keeps 1–3 **chapter briefs** drafted ahead of the
Author — each with a goal, threads to touch, beats to hit, a value shift, and a planned
outcome. The Author treats the current brief as its assignment (honoring it or deviating
deliberately, with the deviation explained in its feed note), and the brief is marked
fulfilled when the chapter lands. The Plotter also owns resolution windows now — it plans
thread payoffs and secret reveals without you lifting a finger (your `plan-resolution` /
`plan-reveal` commands still work and take precedence in its context).

Settings: `plotter_interval` (default 240s) and `plotter_tools_enabled` (default true).

The Outline board lives on Brain tab `5`: the adopted framework and its beats (with
target chapter windows and fulfillment status), a threads × chapters grid with planned
resolution windows and the future runway, and the open briefs marching ahead of the
draft. The Shape tab (`1`) now overlays the blueprint's target tension curve (the dim
`plan` row) under the actual sparkline — divergence raises "tension off-plan" callouts.
Agents can read the whole plan themselves under `/outline/` (blueprint.md, beats.md,
briefs/, threads-plan.md, ledger.md) through their canon file tools.

## 11. Character arcs

The Character Keeper now declares each significant character's **planned arc**: the lie
they believe, what they want versus what they need, and one of five arc types (positive,
flat, disillusionment, fall, corruption). Pivots pin to blueprint beats; advances record
evidence the arc moved; resolution declares how it settled. The **Arcs board** (Brain tab
`6`) shows each active arc's lane — lie → truth, advances, pivots with their beat windows —
and alarms when an arc goes stagnant, misses a pivot window, or resolves against its
declared type (a fall arc that ends in `truth_embraced` is flagged for you to adjudicate,
not auto-corrected: the story may have earned it). The Plotter reads the same findings and
routes stagnant characters into upcoming chapter briefs; it also wakes early when a beat
goes late. The freeform `arc_status` line on each character remains the Keeper's observed
snapshot — the arc aggregate is the plan it's measured against.

## 12. Craft skills and the workspace

Agents no longer carry all their craft knowledge in their prompts. **Skill packs**
ship with novelizer — outlining, promise-payoff, character-arcs, scene-sequel, pacing —
and are mounted read-only at `/skills/`. Every tooled agent sees the *names and
descriptions* of all packs (progressive disclosure keeps that cost to a
handful of lines per pack) until a task actually calls for one; then it reads the pack
body, and pulls reference tables (beat frameworks with their percentage positions,
per-genre obligatory scenes, the arc-type outcome table, the try-fail outcome taxonomy,
tension-curve anchors) on demand. Watch the Engine Room for `⚒ read_file /skills/...`
lines to see a skill activate mid-plan — so asking the Editor "how should I pace this
reveal?" gets an answer grounded in your actual ledger and adopted framework.

Agents also get `/workspace/` — a writable scratch space (per-invocation, not persisted
across turns — there's no checkpointer or thread reuse today) for drafting and comparing
options in files instead of holding everything in one context.
Canon and `/outline/` remain read-only: the only way to change the story record is still a
declared intent through the gated commit path.

Semantic search now covers promises, chapter briefs, and character arcs alongside chapters,
characters, world entries, threads, secrets, and themes — and closed records (paid promises,
superseded briefs, resolved arcs) carry their status in the index, so a search hit never
reads as live guidance when it isn't.

## 13. Framing a story, and finishing one

When you create a story you can **frame** it: pick a structural framework
(`six-position` or `kishotenketsu`), a target chapter count, and a genre. The story
starts with a blueprint already adopted — beats minted with target windows, the Plotter
planning briefs against them from the first tick. Leave the framework blank and the story
begins exactly as before, bottom-up, with the Plotter proposing a shape once there's
enough world to frame.

If the book outgrows (or undershoots) its target, retarget it — `novelizer retarget 32`
from the CLI, `:retarget 32` in Mission Control, or leave it to the Plotter, which may
retarget when the story clearly needs a different length. Beat windows recompute from the
new count everywhere.

A story is **complete** when its blueprint is satisfied: every beat fulfilled, every
promise paid or released, every active arc resolved. The room declares it (`book.completed`)
and the Outline board — now the view Mission Control opens on — shows `✓ COMPLETE` beside
your chapter count. Completion is a statement, not a stop: the Plotter is told the blueprint
is satisfied and steered to write the ending, but nothing forces the room to halt — the
Director closes the story when they're satisfied it's done.
