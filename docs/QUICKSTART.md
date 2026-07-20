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
path). You'll see:

- **LLM base URL** — a text field pre-filled with `http://localhost:8080/v1`; point it at
  your OpenAI-compatible endpoint.
- **API key** — a password field; leave blank for local endpoints that don't require one.
- **Stories directory** — pre-filled with `stories`; where your story directories will
  live.
- **Test connection** — a button; click it to probe the endpoint and populate the model
  pickers below from its live model list.
- **author model** / **agent model** / **embedding model** — three dropdowns, disabled
  until the connection test succeeds; pick a model for each.
- **Save & continue** — writes your global config and proceeds to the story picker (only
  enabled once you've saved or the connection test has populated the model lists).
- **Skip model picks — save endpoint only** — an escape hatch if you just want the endpoint
  saved without picking models yet.

*Verified against: `novelizer/tui/setup_wizard.py`'s actual widget ids
(`base_url`, `api_key`, `stories_dir`, `probe`, `author_model`, `agent_model`,
`embed_model`, `save`, `skip`).*

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

Turning one off reverts that agent to its legacy push-only prompt (no canon tools; the
Author/Checker/chat also revert from the chapter-index map to inline prose excerpts). Use
this if a small local model handles tool-calling poorly. Flag changes take effect on
restart — mid-session edits are deliberately inert until then.
