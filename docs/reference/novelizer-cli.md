# novelizer CLI

`novelizer` is the console script declared in `pyproject.toml`
(`[project.scripts]`, target `novelizer.director.cli:main`). It is a `click`
group (`novelizer/director/cli.py::cli`) with `invoke_without_command=True`:
run bare it boots the TUI; run with a subcommand it operates headlessly
against a story's event store. Headless commands build a store-only
`Runtime` (event store, projector, read store, `ProposalService`) and never
start LLM agents or runners (`_with_runtime`).

## Invocation modes

The `cli` group callback dispatches on `ctx.invoked_subcommand`:

| Invocation | Behavior |
|---|---|
| `novelizer` | Interactive startup: first-run setup wizard (if needed), story picker, then the full TUI (`NovelizerApp`) over a fully started `Runtime` (`_launch_tui`). Quitting the wizard or picker returns without launching the TUI (exit 0). |
| `novelizer <subcommand> ...` | Headless: the group callback resolves a story (see resolution order below) and loads its effective settings into `ctx.obj["settings"]` *before* the subcommand body runs; the subcommand then executes against a store-only `Runtime` and exits. |

Both modes accept the group-level `--story PATH` option. Logging is
configured (`novelizer.logging_setup.configure_logging`) before anything
else in both modes. Because story resolution happens in the group callback,
an invalid `--story` path or a config error fails the command before any
subcommand logic runs.

## Bare invocation: TUI launch (setup wizard, story picker, legacy flat-layout migration prompt)

Running `novelizer` with no subcommand executes `_interactive_startup` in
`novelizer/director/cli.py`, then `_launch_tui`. The steps, in order:

1. **Setup wizard** — runs only when the global config file does not exist
   (`global_config_path()`: `$XDG_CONFIG_HOME/novelizer/config.toml`,
   defaulting to `~/.config/novelizer/config.toml`). `SetupWizardApp`
   collects an LLM base URL, API key, and stories directory, probes the
   endpoint, and offers author/agent/embedding model picks (or
   "Skip model picks — save endpoint only"). Its answers are written as the
   global config (`write_global_config`). Quitting the wizard exits without
   launching the TUI (exit 0).
2. **Story selection**:
   - With `--story PATH`, the path is validated (`is_story_dir`: must
     contain `story.toml` or `world.db`; otherwise a `ClickException`) and
     both the migration prompt and the picker are skipped.
   - Without `--story`:
     - **Legacy flat-layout migration prompt** — if `<stories_root>/world.db`
       exists (a pre-story-directory flat layout) and
       `suppress_flat_migration_prompt` is not set, a confirm prompt
       (default: yes) offers to migrate. Accepting runs
       `migrate_flat_layout`: `world.db` and the `chroma/` directory move
       into `<stories_root>/default/`, and a `story.toml` (title `default`)
       is written there if missing; migration errors out if `default/`
       already holds a story (`FileExistsError`, surfaced as a
       `ClickException`). Declining writes
       `suppress_flat_migration_prompt = true` to the global config so the
       prompt never recurs. Either way, startup continues to the picker.
     - **Story picker** — `StoryPickerApp` lists story directories under the
       stories root (last-opened story first, remainder most-recently-written
       first) plus a "New story" option that creates one inline
       (`create_story`, with optional seed and framing inputs). Quitting the
       picker exits without launching the TUI (exit 0).
3. **Launch** — the chosen story is recorded as `last_opened_story` in the
   global config, effective settings are reloaded with the story's own
   settings layered in (`load_effective_settings(story_dir=...)`), and
   `_launch_tui` starts a full `Runtime` (LLM agents and runners included —
   unlike headless commands) and runs `NovelizerApp` over it, closing the
   runtime on exit.

`<stories_root>` is `default_stories_dir` from settings, `~`-expanded.
Config or migration failures (`TOMLFileError`, `StoryConfigError`,
`FileExistsError`) print `Error: <message>` and exit nonzero — see
[Exit and error behavior summary](#exit-and-error-behavior-summary).

## Global option `--story PATH` and story resolution order (`--story` -> last-opened -> legacy flat migration -> `stories/default`)

`--story` is the only group-level option:

```
novelizer [--story PATH] [SUBCOMMAND ...]
```

It must precede the subcommand (it belongs to the `cli` group, not to any
subcommand). The path is `~`-expanded and must be a story directory —
`is_story_dir` (`novelizer/settings/story_dir.py`) requires `story.toml` or
`world.db` directly inside it — otherwise the command fails with
`Error: <path> is not a story directory (no story.toml or world.db). ...`
(a `ClickException`, nonzero exit). `--story` applies to both modes: on bare
invocation it bypasses the story picker and the migration prompt; on
headless invocation it is step 1 of the resolution order below.

For headless subcommands, `_resolve_story` (`novelizer/director/cli.py`)
picks the story in this order — the first rule that produces a story wins:

1. **`--story PATH`** — validated as above; an invalid path is an error, not
   a fall-through.
2. **`last_opened_story`** — the global-config value, used only if it still
   points at a valid story directory. A stale or deleted path falls through
   silently to the next rule
   (`tests/director/test_resolve_story.py::test_stale_last_opened_falls_through`).
3. **Legacy flat migration** — if `<stories_root>/world.db` exists (a
   pre-story-directory flat layout):
   - with `suppress_flat_migration_prompt` set, the flat root itself is used
     as the story, no prompt;
   - otherwise a confirm prompt (default: yes) offers migration. Accepting
     runs `migrate_flat_layout`, which moves `world.db` and `chroma/` into
     `<stories_root>/default/`, writes a `story.toml` (title `default`) if
     missing, and uses that directory; it raises `FileExistsError` (surfaced
     as `Error: ...`, nonzero exit) if `default/` already holds a story.
     Declining writes `suppress_flat_migration_prompt = true` to the global
     config and uses the flat root, so legacy paths keep working and the
     prompt never recurs.
4. **`<stories_root>/default`** — used if it is already a story directory;
   otherwise created on the spot via `create_story` with title `default`
   (`tests/director/test_resolve_story.py::test_fresh_install_creates_default_story`).

`<stories_root>` is `default_stories_dir` from the effective settings
(default `stories`), `~`-expanded. See
[configuration.md](configuration.md) for `default_stories_dir`,
`last_opened_story`, and `suppress_flat_migration_prompt`.

After resolution, a headless subcommand records the chosen story as
`last_opened_story` — but only when the global config file already exists.
It never creates the file, so a fresh user's first-run setup wizard is not
suppressed by having run a headless command
(`tests/director/test_cli.py::test_headless_subcommand_does_not_create_global_config_when_absent`;
the companion test
`test_headless_subcommand_still_records_last_opened_when_config_exists`
guards the update path).

## Subcommands at a glance

| Command | Arguments | Purpose |
|---|---|---|
| `seed` | `TEXT` | Inject a narrative seed signal |
| `chapters` | — | List chapters by editorial status |
| `read` | `CHAPTER_ID` | Print a chapter's prose |
| `retcons` | — | List open contradiction flags |
| `proposals` | — | List pending (open) proposals |
| `approve` | `PROPOSAL_ID` | Approve a pending proposal |
| `reject` | `PROPOSAL_ID` | Reject a pending proposal |
| `voices` | `[--pack PATH]` | Show a voice pack's profiles and voice cards |
| `voice-scaffold` | `PROFILE_NAME DESCRIPTION [--pack PATH]` | Scaffold a prose profile into a user pack |
| `autonomy` | `LEVEL [AGENT]` | Set global or per-agent autonomy |
| `plan-resolution` | `THREAD_ID WINDOW_LO WINDOW_HI [--note TEXT]` | Set/clear a thread's resolution window |
| `plan-reveal` | `SECRET_ID WINDOW_LO WINDOW_HI` | Set/clear a secret's reveal window |
| `retarget` | `TARGET_CHAPTERS` | Retarget the active blueprint's chapter count |

Listing tables truncate IDs to their first 8 characters for display; the
commands that take an ID (`read`, `approve`, `reject`, `plan-resolution`,
`plan-reveal`) look records up by exact full ID.

## `novelizer seed TEXT`

```
novelizer [--story PATH] seed TEXT
```

Injects a narrative seed into the resolved story: appends a
`director_signal.created` event whose payload is a new `DirectorSignal`
(`novelizer/store/models.py`) with `kind=seed`, `body=TEXT`, a fresh UUID
`id`, and `consumed=false` (`novelizer/director/commands.py::seed`). On
success it prints `Seed injected: TEXT` (green) and exits 0.

- `TEXT` is a single required argument — quote multi-word seeds:
  `novelizer seed "a storm is coming"`.
- Director signals are never gated (`_NEVER_GATED` in
  `novelizer/canon/policy.py`), so the event commits directly to the store
  at every autonomy level; no proposal is created.
- The headless runtime starts no agents, so the signal simply sits in the
  store as an unconsumed director signal until the next TUI session. CLI
  seeds carry no `target_agent`, which makes them visible to every
  signal-reading agent (`ReadStore.list_unconsumed_signals`) — the World
  Architect, Author, and Plotter all read pending signals, and whichever
  acts on them first appends a `director_signal.consumed` event marking
  them done (`BaseAgent._consume_signals`).
- This is the same signal the TUI's `:seed` chat command emits; seeds
  entered when creating a story in the picker also land as this event
  (`commands.seed_story_dir`).

## `novelizer chapters`

```
novelizer [--story PATH] chapters
```

Lists every chapter in the resolved story as a table titled `Chapters`
(`novelizer/director/cli.py::chapters`), or prints `No chapters yet.` when
the story has none. The command takes no arguments or options.

| Column | Content |
|---|---|
| ID | First 8 characters of the chapter ID (dimmed; the full ID is what `novelizer read` requires) |
| Title | Chapter title |
| Status | The chapter's `editorial_status` value: `draft`, `reviewed`, or `final` (`EditorialStatus` in `novelizer/store/models.py`) |

Rows come from `ReadStore.list_chapters()` with no status filter, ordered by
projection row order (`ORDER BY rowid`). The projector writes chapter rows
with `INSERT OR REPLACE` (`novelizer/canon/projector.py`), which re-inserts
the row on every update — so the listing is ordered by each chapter's most
recent projection write (creation, `chapter.status_changed`, or
`chapter.revised`), not strictly by creation order: a chapter that is
revised or has its status changed moves to the bottom of the table. A
`chapter.revised` event also resets the chapter's `editorial_status` to
`draft` and bumps its revision count, so a revised chapter's Status column
drops back to `draft`.

Always exits 0. To read a listed chapter's prose, pass its *full* ID to
[`novelizer read`](#novelizer-read-chapter_id) — the 8-character display
prefix is not accepted.

## `novelizer read CHAPTER_ID`

```
novelizer [--story PATH] read CHAPTER_ID
```

Prints one chapter's prose to stdout (`novelizer/director/cli.py::read`):
the chapter title rendered as a horizontal rule (`console.rule`), followed
by the chapter's current `prose` text. Output is plain prose — no paging,
no wrapping beyond the terminal's, no metadata beyond the title rule.

- `CHAPTER_ID` must be the **full** chapter ID. Lookup is an exact-match
  query on the projection (`ReadStore.get_chapter`:
  `SELECT ... FROM chapters WHERE id=?`) — the 8-character prefix shown by
  [`novelizer chapters`](#novelizer-chapters) is display-only and will not
  match.
- An unknown ID prints `Chapter <id> not found.` (red) and exits 0, like
  all domain rejections — check the output text, not the exit code.
- The prose printed is the chapter's latest canon state: a
  `chapter.revised` event replaces the projected row's `prose` in place
  (`novelizer/canon/projector.py`), so `read` always shows the
  post-revision text, never the original draft.

Always exits 0. To capture a chapter to a file:
`novelizer read <full-id> > chapter.txt` (the title rule is included in
stdout).

## `novelizer retcons`

```
novelizer [--story PATH] retcons
```

Lists the story's open contradiction flags
(`novelizer/director/cli.py::retcons`) as a table titled
`Open Retcon Requests`, or prints `No open retcon requests.` when there are
none. The command takes no arguments or options and always exits 0.

| Column | Content |
|---|---|
| ID | First 8 characters of the flag ID (dimmed) |
| Description | The flag's `description` |
| Proposed Resolution | The flag's `proposed_resolution` (may be empty) |

Rows are `ReadStore.list_flags(category="contradiction", status="open")` —
the `Flag` records (`novelizer/store/models.py`) filed with category
`contradiction` whose status is still `open`, ordered by projection
insertion order (`ORDER BY rowid`). Flags in any other status (`resolved`,
`rejected`, `stale`) or any other category (`pacing`, `thematic`, etc. —
`category` is free-form) never appear here.

Two event families feed this listing (`novelizer/canon/projector.py`):

- `flag.created` / `flag.resolved` / `flag.rejected` — the current flag
  events, filtered to `category="contradiction"`;
- the legacy `retcon_request.*` events from pre-Flag databases, which the
  projector aliases into the flags table as `category="contradiction"` —
  old event logs list correctly without migration.

This command is read-only: flags are filed by agents (Continuity Checker,
Character Keeper, and others via `flag.created`) and closed by agents (the
Retconner resolves or rejects contradiction flags; Triage rejects dismissed
or stale ones) — there is no headless command to file or close one, and the
TUI's human action on flags is limited to clearing escalations
(`flag.escalation_cleared`, `novelizer/tui/escalations_screen.py`). To gate
how retcon-class *fixes* enter canon, set autonomy to
`gated_retcons` or stricter (see
[`novelizer autonomy`](#novelizer-autonomy-level-agent)); the resulting
proposals surface under [`novelizer proposals`](#novelizer-proposals).

## `novelizer proposals`

```
novelizer [--story PATH] proposals
```

Lists the story's pending proposals (`novelizer/director/cli.py::proposals`)
as a table titled `Pending Proposals`, or prints `No pending proposals.`
when there are none. The command takes no arguments or options and always
exits 0.

| Column | Content |
|---|---|
| ID | First 8 characters of the proposal ID (dimmed; `approve`/`reject` need the full ID) |
| Agent | `proposing_agent` — the agent whose gated action created the proposal |
| Target Event | `target_event_type` — the event type that will enter canon on approval |

Rows are `ReadStore.list_proposals(status="open")` — an exact filter on the
projection's status column, ordered by creation order (`ORDER BY rowid`; a
decision updates the row's status in place, so ordering is stable —
`novelizer/canon/projector.py`). `approved` and `rejected` proposals never
appear here; there is no CLI flag to list them.

A proposal is a `Proposal` record (`novelizer/canon/autonomy.py`): besides
the displayed fields it carries the target aggregate ID and the full event
payload that will be appended verbatim on approval. Proposals are created
by the `GatingCommitter` (`novelizer/canon/committer.py`): when the current
autonomy policy gates an agent's event, the committer appends a
`proposal.created` event *instead of* the target event — the queue this
command shows is exactly the set of agent actions awaiting a human
decision. Which actions get gated is controlled by
[`novelizer autonomy`](#novelizer-autonomy-level-agent); the rationale and
tier semantics are explained in
[how-the-room-works.md](../explanation/how-the-room-works.md) (Proposals and
the human approval loop).

To act on a listed proposal, pass its **full** ID to
[`novelizer approve`](#novelizer-approve-proposal_id) or
[`novelizer reject`](#novelizer-reject-proposal_id) — lookup is exact
(`ReadStore.get_proposal`: `SELECT ... WHERE id=?`), so the 8-character
display prefix is not accepted. The TUI's approval screen offers the same
decisions interactively without needing the ID.

## `novelizer approve PROPOSAL_ID`

```
novelizer [--story PATH] approve PROPOSAL_ID
```

Approves an open proposal (`novelizer/director/cli.py::approve` →
`commands.approve` → `ProposalService.approve`,
`novelizer/canon/proposal_service.py`). Approval appends **two** events:

1. the proposal's target event — `target_event_type` with the stored
   payload, appended verbatim (`EventStore.append_raw`) under the original
   target aggregate ID, exactly as if the agent had committed it ungated;
2. a `proposal.approved` event carrying the proposal with
   `status=approved`.

Approving and rejecting are the only two ways a proposal leaves the `open`
state (`ProposalService` docstring). The target event is appended straight
to the event store, not routed back through the gating committer
(`novelizer/canon/committer.py`), so an approved event enters canon even
under `gated_all` — approval cannot re-gate itself. The read model picks
both events up at the next projector catch-up, which every headless
invocation runs at startup: approving, say, a gated `chapter.created`
proposal makes the chapter appear in the next
[`novelizer chapters`](#novelizer-chapters) run
(`tests/director/test_cli.py::test_approve_command_approves_and_reports`
verifies the approved status and the materialized target event through a
fresh read store).

Output (printed green) is one of:

| Output | Meaning |
|---|---|
| `Approved proposal <id> (<target_event_type>)` | Success — both events appended |
| `Proposal not found: <id>` | No proposal with that exact ID |
| `Proposal <id> is already <approved\|rejected>.` | Already decided — nothing appended; decisions are not repeatable or reversible |

All three exit 0 — scripts must check the output text, not the exit code
(see [Exit and error behavior summary](#exit-and-error-behavior-summary)).

- `PROPOSAL_ID` must be the **full** ID; the 8-character prefix shown by
  [`novelizer proposals`](#novelizer-proposals) is display-only
  (`ReadStore.get_proposal` is an exact `WHERE id=?` lookup).
- There is no partial approval or payload editing: the event enters canon
  with exactly the payload the agent proposed. To keep it out of canon,
  use [`novelizer reject`](#novelizer-reject-proposal_id).
- The decision is logged at INFO level
  (`approved proposal <id> (<target_event_type>)`).

What gets proposed instead of committed is governed by the autonomy policy
— see [`novelizer autonomy`](#novelizer-autonomy-level-agent) for the
levels and
[how-the-room-works.md](../explanation/how-the-room-works.md) (Proposals
and the human approval loop) for approval semantics.

## `novelizer reject PROPOSAL_ID`

```
novelizer [--story PATH] reject PROPOSAL_ID
```

Rejects an open proposal (`novelizer/director/cli.py::reject` →
`commands.reject` → `ProposalService.reject`,
`novelizer/canon/proposal_service.py`). Rejection appends **one** event: a
`proposal.rejected` carrying the proposal with `status=rejected`. The
proposal's target event is never appended — the agent's proposed action
stays out of canon
(`tests/canon/test_proposal_service.py::test_reject_marks_rejected_without_target_event`).
In the read model the projector updates the proposal row's status in place
(`novelizer/canon/projector.py`), so the proposal drops out of
[`novelizer proposals`](#novelizer-proposals) at the next catch-up
(`tests/director/test_cli.py::test_reject_command_rejects_and_reports`).

Output (printed yellow — including success) is one of:

| Output | Meaning |
|---|---|
| `Rejected proposal <id> (<target_event_type>)` | Success — `proposal.rejected` appended, target event discarded |
| `Proposal not found: <id>` | No proposal with that exact ID |
| `Proposal <id> is already <approved\|rejected>.` | Already decided — nothing appended |

All three exit 0 — scripts must check the output text, not the exit code
(see [Exit and error behavior summary](#exit-and-error-behavior-summary)).

- `PROPOSAL_ID` must be the **full** ID; the 8-character prefix shown by
  [`novelizer proposals`](#novelizer-proposals) is display-only
  (`ReadStore.get_proposal` is an exact `WHERE id=?` lookup).
- Rejection is final: approving and rejecting are the only two ways a
  proposal leaves the `open` state, and `ProposalService`'s open-status
  guard makes a later `approve` of the rejected proposal a no-op — its
  target event can never materialize afterwards
  (`tests/canon/test_proposal_service.py::test_reject_then_approve_is_noop`).
  If the underlying action is still wanted, the agent must propose it
  again.
- Nothing notifies the proposing agent of the rejection — no agent
  consumes `proposal.rejected` (the projector alone processes it, to update
  the read model's status column); the proposal simply leaves the open
  queue.
  To steer what the agent tries next, follow up with a
  [`novelizer seed`](#novelizer-seed-text) signal.
- The decision is logged at INFO level
  (`rejected proposal <id> (<target_event_type>)`).

See [how-the-room-works.md](../explanation/how-the-room-works.md)
(Proposals and the human approval loop) for when rejecting is the right
call versus loosening the gate with
[`novelizer autonomy`](#novelizer-autonomy-level-agent).

## `novelizer voices [--pack PATH]`

```
novelizer [--story PATH] voices [--pack PATH]
```

Prints a plain-text report of a voice pack — its prose profiles, agent
personalities, and the resolved story's character voice cards
(`novelizer/director/cli.py::voices`). The report is built by the pure
formatter `format_voice_report(pack, characters, active_profile)` in the
same module and printed via Rich. The command is read-only: no events are
appended.

Report layout, in order:

| Block | Content |
|---|---|
| `Voice pack: <name>` | The pack's `name` field |
| `Prose profiles:` | One line per profile: `<name>: <casting-note snippet>`; the active profile is prefixed `* `, all others with two spaces |
| `Agent personalities:` | One line per agent: `<agent>: <note snippet>` |
| `Character voices:` | One line per character: `<name>: <voice snippet>` — only characters of the resolved story with a non-empty `voice` field; the block is omitted entirely when there are none |

Every snippet is the source text stripped, with newlines collapsed to
spaces, truncated to its first 80 characters — the report is a cast list,
not the full casting notes.

**Pack selection.** Without `--pack`, the active pack from settings
`voice_pack` is loaded, and the settings `prose_profile` value determines
which profile gets the `*` marker
(`tests/director/test_cli.py::test_voices_lists_default_pack_profiles`).
With `--pack PATH`, that pack file is inspected instead and **no** profile
is marked active — the active profile belongs to the configured pack, not
the inspected one (`active_name` is passed as `None`;
`tests/director/test_cli.py::test_voices_with_explicit_pack_path`). See
[configuration.md](configuration.md) for `voice_pack` and `prose_profile`.

- The default `voice_pack` is the shipped pack
  (`novelizer/voices/default.toml`, resolved via `importlib.resources`),
  whose profiles are `sparse`, `lush`, and `plain` and which carries
  personality notes for seven agents (`author`, `editor`, `world_architect`,
  `character_keeper`, `continuity_checker`, `retconner`, `plotter`); the
  default `prose_profile` is `plain`.
- A pack file is TOML (`load_voice_pack`, `novelizer/voices/loader.py`): a
  top-level `name`, `[prose_profiles.<key>]` tables (`name`,
  `casting_note`), and an optional `[agent_personalities]` table.
  [`novelizer voice-scaffold`](#novelizer-voice-scaffold-profile_name-description---pack-path)
  writes profile blocks in this format.
- Character voice cards always come from the **resolved story**'s read
  model (`ReadStore.list_characters`), even when `--pack` inspects some
  other pack file — story resolution runs regardless.
- A pack path that does not exist raises
  `FileNotFoundError: Voice pack not found at '<path>'.`
  (`tests/voices/test_loader.py::test_missing_file_raises_clear_error`).
  Unlike domain rejections, this is not caught by the CLI: it surfaces as
  a traceback with a nonzero exit.

## `novelizer voice-scaffold PROFILE_NAME DESCRIPTION [--pack PATH]`

```
novelizer [--story PATH] voice-scaffold PROFILE_NAME DESCRIPTION [--pack PATH]
```

Writes a `[prose_profiles.<PROFILE_NAME>]` block into a user voice-pack TOML
file (`novelizer/director/cli.py::voice_scaffold` →
`novelizer/voices/scaffold.py::scaffold_prose_profile`), creating the file
if it does not exist. No LLM call is made: `DESCRIPTION` becomes the
profile's `casting_note` **verbatim** — quote it
(`novelizer voice-scaffold brisk "Fast, punchy, present-tense action prose."`).
This is a pure file operation: no events are appended and the event store is
never opened (story resolution still runs in the group callback, so an
invalid `--story` fails first).

Arguments and validation:

- `PROFILE_NAME` must match `^[A-Za-z0-9_-]+$` (letters, digits, hyphens,
  underscores). Anything else — spaces, dots, brackets — is rejected with
  `Invalid profile name '<name>': use only letters, digits, hyphens, and
  underscores.` and the pack file is left untouched
  (`tests/voices/test_scaffold.py::test_scaffold_rejects_invalid_profile_name`).
- `--pack` defaults to the literal path `stories/user_pack.toml`, resolved
  against the current working directory (it is *not* derived from
  `default_stories_dir` — the default only lines up with the stories root
  when that setting is at its own default, `stories`).
- The shipped default pack (`novelizer/voices/default.toml`) is refused as a
  target: a `--pack` path that resolves to it fails with
  `Refusing to scaffold into the shipped default voice pack; pass a separate
  user pack path instead.`
  (`tests/voices/test_scaffold.py::test_scaffold_refuses_to_write_the_shipped_default_pack`).

These rejections print the error in red and exit 0, like all domain
rejections (see
[Exit and error behavior summary](#exit-and-error-behavior-summary)) — as
does an existing `--pack` file that is not valid TOML, since the CLI
catches `ValueError` and `tomllib.TOMLDecodeError` is one. On success
(green): `Scaffolded profile '<name>' into <path>`.

Write semantics (`scaffold_prose_profile`):

- **New file** — a fresh pack is created with `name` set to the file's stem
  (`user_pack` for the default path) and the one profile block. Each profile
  block carries `name = "<PROFILE_NAME>"` and
  `casting_note = "<DESCRIPTION>"`.
- **Existing file** — the pack is read with stdlib `tomllib` and rewritten
  with the new profile added. Other profiles and the
  `[agent_personalities]` table are preserved
  (`test_scaffold_appends_to_an_existing_user_pack_without_clobbering_other_profiles`);
  re-scaffolding an existing name replaces that profile's casting note in
  place (`test_scaffold_is_idempotent_replacing_same_named_profile`).
- The file is rewritten by a hand-written serializer for the fixed
  `VoicePack` shape (top-level `name`, `[prose_profiles.<key>]` tables,
  `[agent_personalities]`) — TOML comments and any keys outside that shape
  in a hand-edited pack are **not** preserved across a scaffold. Quotes,
  backslashes, and newlines in `DESCRIPTION` are escaped so the written
  value stays a valid single-line TOML string
  (`test_scaffold_escapes_quotes_and_backslashes_in_description`).

Scaffolding does not activate anything. To use the profile, point settings
`voice_pack` at the pack file and `prose_profile` at the profile name — see
[configuration.md](configuration.md) — then confirm with
[`novelizer voices`](#novelizer-voices---pack-path) (the new profile gains
the `*` marker once active). Packs written to the default location are also
picked up by the TUI's new-story voice-pack picker, which lists `*.toml`
files directly under the stories root
(`novelizer/voices/discovery.py::discover_voice_packs`).

## `novelizer autonomy LEVEL [AGENT]`

```
novelizer [--story PATH] autonomy LEVEL [AGENT]
```

Sets the story's autonomy level — globally, or as a per-agent override
(`novelizer/director/cli.py::autonomy`). The command reads the current
`AutonomyState` from the read model (`ReadStore.get_autonomy_state`;
defaults to `full_auto` with no overrides when nothing has been set),
builds the successor state, and appends a single `autonomy.changed` event
carrying the **complete** new state — global level plus all overrides —
under the fixed aggregate ID `singleton`
(`commands.autonomy` → `EventStore.append`). The projector keeps only the
latest state (`INSERT OR REPLACE` into the one-row `autonomy_state` table,
`novelizer/canon/projector.py`), so each invocation wholly replaces the
previous configuration snapshot.

| Invocation | Effect | Output (green) |
|---|---|---|
| `novelizer autonomy LEVEL` | Sets the global level; **all existing per-agent overrides are preserved** | `Global autonomy set to <level>` |
| `novelizer autonomy LEVEL AGENT` | Sets (or replaces) the override for that agent name; the global level and other agents' overrides are preserved | `Autonomy for <agent> set to <level>` |

`LEVEL` must be one of the four `AutonomyLevel` values, lowercase and exact:
`full_auto`, `gated_retcons`, `gated_canon`, `gated_all` — see
[Autonomy levels](#autonomy-levels-full_auto-gated_retcons-gated_canon-gated_all)
for what each gates. Anything else prints
`Unknown autonomy level: <level>` (red) and exits 0 with **no event
appended**
(`tests/director/test_cli.py::test_autonomy_command_rejects_unknown_level_with_friendly_message`).
All outcomes exit 0 — check the output text (see
[Exit and error behavior summary](#exit-and-error-behavior-summary)).

`AGENT` is a free-form string, **not validated** against the roster: the
real names are the `AgentSpec.name` values — `author`, `editor`, `plotter`,
`muse`, `world_architect`, `character_keeper`, `continuity_checker`,
`structure_analyst`, `retconner`, `triage` — and a misspelled name silently
creates an override no agent ever consults. There is no CLI verb to
*remove* an override: once set, an agent stops following the global level
permanently (`AutonomyState.level_for` returns the override when present,
`novelizer/canon/autonomy.py`); the closest workaround is re-running the
command to point the override at the level you want.

The change takes effect on each agent's *next* commit attempt: the gating
committer asks `AutonomyPolicy.is_gated(agent_name, event_type)`
(`novelizer/canon/policy.py`), which re-reads the live state per decision —
no restart needed, though a running TUI session's projector must catch up
first. Gated commits become proposals (see
[`novelizer proposals`](#novelizer-proposals)). The `autonomy.changed`
event itself is appended by the CLI directly to the store, so changing
autonomy is never itself gated — even under `gated_all` you can always
loosen the gate again.

The TUI chat command `:autonomy LEVEL [AGENT]` performs the identical
read-modify-write (`novelizer/director/commands.py::_cmd_autonomy`); both
paths append through the same `commands.autonomy` helper.

## `novelizer plan-resolution THREAD_ID WINDOW_LO WINDOW_HI [--note TEXT]`

```
novelizer [--story PATH] plan-resolution THREAD_ID WINDOW_LO WINDOW_HI [--note TEXT]
```

Sets a plot thread's planned resolution window, or clears it with `0 0`
(`novelizer/director/cli.py::plan_resolution` →
`commands.plan_thread_resolution`). On success it appends one
`thread.resolution_planned` event (`ThreadResolutionPlanned`,
`novelizer/canon/events.py`) under the thread's aggregate ID, carrying
`window_lo`, `window_hi`, and the `--note` text as
`planned_payoff_note` (default `""`; help: "Optional planned-payoff
note."). The projector updates the thread row's `window_lo` /
`window_hi` / `planned_payoff_note` fields in place
(`novelizer/canon/projector.py`), leaving the thread's state, touch count,
and last touch note untouched.

Window semantics:

- `WINDOW_LO` and `WINDOW_HI` are **1-based chapter ordinals** — "resolve
  this thread somewhere between chapter LO and chapter HI".
- Valid windows are `0 0` (clear the plan) or `1 <= WINDOW_LO <= WINDOW_HI`.
  Anything else — `9 3`, `0 5`, negative values — is rejected.
- Re-planning **supersedes**: each event wholly replaces the previous
  window and note; the event history is the record of schedule slips
  (`ThreadResolutionPlanned` docstring). There is no separate "clear"
  event — `0 0` is a plan whose window is empty, and it also resets the
  payoff note (to `""`, or to `--note` if given). The success message for
  a clear therefore reads `resolution window ch0-0 planned for '...'`.

Outcomes (all exit 0 — check the output text, see
[Exit and error behavior summary](#exit-and-error-behavior-summary)):

| Output | Color | Meaning |
|---|---|---|
| `resolution window ch<lo>-<hi> planned for '<thread name>'` | green | Event appended (`tests/director/test_cli.py::test_plan_resolution_valid_window_reports_success`) |
| `no such thread: <id>` | yellow | No thread with that exact ID — nothing appended |
| `thread <id> is already <paid_off\|abandoned>` | yellow | Terminal thread (`TERMINAL_STATES`, `novelizer/canon/threads.py`) — resolved threads cannot be re-planned |
| `invalid window <lo>-<hi> (need 1 <= lo <= hi, or 0 0 to clear)` | yellow | Window rule violated (`tests/director/test_cli.py::test_plan_resolution_invalid_window_reports_rejection`) |

The CLI colors by string prefix: results starting `resolution window` are
green, everything else yellow. Both window arguments are `click`
`type=int`: a non-integer is a usage error (exit 2), unlike the domain
rejections above.

- `THREAD_ID` must be the **full** thread ID (`ReadStore.get_thread` is an
  exact `WHERE id=?` lookup). The CLI has no thread-listing command; full
  IDs come from the event log (`thread.planted` events) or from the TUI's
  Story Brain data — threads are planted by agents, not by any headless
  command.
- `thread.resolution_planned` is never gated (`_NEVER_GATED`,
  `novelizer/canon/policy.py`): planning is scheduling bookkeeping, not
  canon, so it commits directly at every autonomy level and no proposal is
  created.
- The window is advisory pacing state, consumed by the resolution-pacing
  checks (`novelizer/brain/resolution_pacing.py`) that feed both agent
  context assembly (`novelizer/brain/context.py`) and the TUI's Story
  Brain panels: a non-terminal thread with `window_hi > 0` becomes
  *overdue* once the story's chapter count exceeds `window_hi`, and
  overlapping thread-resolution/secret-reveal windows holding more than
  two payoffs are reported as *congested*. Nothing forces an agent to
  resolve the thread inside the window.
- The Plotter can plan windows itself: agent `resolution_plan_intents`
  (`ResolutionPlanIntent`, `novelizer/agents/schemas.py`) emit the same
  event through the committer (`novelizer/agents/intents.py`) — a later
  agent plan will overwrite yours, and vice versa.

The secret-side counterpart is
[`novelizer plan-reveal`](#novelizer-plan-reveal-secret_id-window_lo-window_hi),
which shares the window rule.

## `novelizer plan-reveal SECRET_ID WINDOW_LO WINDOW_HI`

```
novelizer [--story PATH] plan-reveal SECRET_ID WINDOW_LO WINDOW_HI
```

Sets a secret's planned reveal window, or clears it with `0 0`
(`novelizer/director/cli.py::plan_reveal` →
`commands.plan_secret_reveal`). On success it appends one
`secret.reveal_planned` event (`SecretRevealPlanned`,
`novelizer/canon/events.py`) under the secret's aggregate ID, carrying
`window_lo` and `window_hi`. The projector updates the secret row's
`reveal_window_lo` / `reveal_window_hi` fields in place
(`novelizer/canon/projector.py`), leaving `revealed` and the knowledge
matrix untouched. Unlike `plan-resolution`, there is no `--note` option —
the payload is just the window.

Window semantics (identical rule to
[`novelizer plan-resolution`](#novelizer-plan-resolution-thread_id-window_lo-window_hi---note-text)):

- `WINDOW_LO` and `WINDOW_HI` are **1-based chapter ordinals** — "reveal
  this secret somewhere between chapter LO and chapter HI".
- Valid windows are `0 0` (clear the plan) or `1 <= WINDOW_LO <= WINDOW_HI`.
- Re-planning **supersedes**: each event wholly replaces the previous
  window (`SecretRevealPlanned` docstring). There is no separate "clear"
  event — `0 0` is a plan whose window is empty, and the success message
  for a clear reads `reveal window ch0-0 planned for '...'`.

Outcomes (all exit 0 — check the output text, see
[Exit and error behavior summary](#exit-and-error-behavior-summary)):

| Output | Color | Meaning |
|---|---|---|
| `reveal window ch<lo>-<hi> planned for '<secret title>'` | green | Event appended (`tests/director/test_cli.py::test_plan_reveal_valid_window_reports_success`) |
| `no such secret: <id>` | yellow | No secret with that exact ID — nothing appended (`tests/director/test_cli.py::test_plan_reveal_unknown_secret_reports_rejection`) |
| `secret <id> is already revealed` | yellow | The secret's `revealed` flag is set — reveals are set-once, so a revealed secret cannot be re-planned; the existing window stays untouched (`tests/director/test_commands.py::test_plan_secret_reveal_appends_and_rejects_revealed`) |
| `invalid window <lo>-<hi> (need 1 <= lo <= hi, or 0 0 to clear)` | yellow | Window rule violated |

The CLI colors by string prefix: results starting `reveal window` are
green, everything else yellow. Both window arguments are `click`
`type=int`: a non-integer is a usage error (exit 2), unlike the domain
rejections above.

- `SECRET_ID` must be the **full** secret ID (`ReadStore.get_secret` is an
  exact `WHERE id=?` lookup). The CLI has no secret-listing command; full
  IDs come from the event log (`secret.created` events) or from the TUI's
  Story Brain data — secrets are minted by agents, not by any headless
  command.
- `secret.reveal_planned` is never gated (`_NEVER_GATED`,
  `novelizer/canon/policy.py`): like thread planning, it is scheduling
  bookkeeping, so it commits directly at every autonomy level and no
  proposal is created.
- The window is advisory pacing state, consumed by the resolution-pacing
  checks (`novelizer/brain/resolution_pacing.py`) that feed both agent
  context assembly (`novelizer/brain/context.py`) and the TUI's Story
  Brain panels: an unrevealed secret with `reveal_window_hi > 0` becomes
  *overdue* once the story's chapter count exceeds `reveal_window_hi`
  (`overdue_reveals`),
  and its window counts toward the same *congested-windows* check as
  thread-resolution windows (more than two payoffs in overlapping windows
  by default). Nothing forces an agent to reveal the secret inside the
  window.
- The Plotter can plan reveal windows itself: agent
  `ResolutionPlanIntent`s with `kind="secret"` emit the same event through
  the committer (`novelizer/agents/intents.py::commit_resolution_plan_intents`)
  — a later agent plan will overwrite yours, and vice versa.

The thread-side counterpart is
[`novelizer plan-resolution`](#novelizer-plan-resolution-thread_id-window_lo-window_hi---note-text).

## `novelizer retarget TARGET_CHAPTERS`

```
novelizer [--story PATH] retarget TARGET_CHAPTERS
```

Changes the active blueprint's target chapter count — the book is running
long or short (`novelizer/director/cli.py::retarget` →
`commands.retarget_blueprint`). On success it appends one
`blueprint.retargeted` event (`BlueprintRetargeted`,
`novelizer/canon/events.py`) under the active blueprint's aggregate ID,
carrying `blueprint_id` and the new `target_chapter_count`. The projector
updates the active blueprint row's `target_chapter_count` in place
(`novelizer/canon/projector.py`); events citing an unknown or superseded
blueprint ID are projection no-ops.

Retargeting does not touch the beats themselves: beats store *percentage*
positions (`ideal_pct`, `tolerance_pct`), and their chapter windows are
recomputed from the current `target_chapter_count` in read-side logic
(`beat_window`, `novelizer/canon/beat_templates.py`, used by the Story
Brain's beat-drift, next-beat, and arc-alignment checks; tension targets
and completion checks read the count directly — `novelizer/brain/`). Retargeting therefore reflows the
whole pacing plan in one event.

Outcomes (all exit 0 — check the output text, see
[Exit and error behavior summary](#exit-and-error-behavior-summary)):

| Output | Color | Meaning |
|---|---|---|
| `blueprint retargeted to <N> chapters` | green | Event appended (`tests/director/test_cli.py::test_retarget_command_sets_target_chapter_count`) |
| `no active blueprint to retarget` | yellow | The story has no active blueprint — nothing to retarget (`tests/director/test_cli.py::test_retarget_command_rejects_no_active_blueprint`) |
| `invalid target_chapter_count <N> (need >= 3)` | yellow | Fewer than 3 chapters cannot place a beat sequence meaningfully |
| `blueprint is already targeted at <N> chapters -- no change` | yellow | Value equals the current target — a no-op; no event is appended (`tests/director/test_commands.py::test_retarget_blueprint_no_op_when_target_unchanged`) |

The CLI colors by string prefix: results starting `blueprint retargeted`
are green, everything else yellow. `TARGET_CHAPTERS` is a `click`
`type=int` argument: a non-integer is a usage error (exit 2), unlike the
domain rejections above.

- `blueprint.retargeted` is never gated (`_NEVER_GATED`,
  `novelizer/canon/policy.py`): it commits directly at every autonomy
  level and no proposal is created. This is deliberately asymmetric with
  blueprint *adoption*, which is always gated — choosing the structural
  framework needs sign-off, but resizing an already-approved plan is
  pacing bookkeeping.
- There is no CLI command to inspect the blueprint or its current target;
  the active blueprint and its beat windows are visible in the TUI's Story
  Brain. Blueprints are adopted at story creation (the picker's Frame
  step) or via an agent-proposed, always-gated `blueprint.adopted`.
- The TUI chat command `:retarget N` performs the identical transition
  through the same helper (`commands._cmd_retarget`; a non-integer prints
  `Invalid chapter count: <arg>`).
- The Plotter can retarget on its own: an agent-declared `RetargetIntent`
  (`novelizer/agents/schemas.py`) commits the same event through
  `commit_retarget_intent` (`novelizer/agents/intents.py`), with the same
  `>= 3` and no-change guards — a later agent retarget will overwrite
  yours, and vice versa.

## Autonomy levels (full_auto, gated_retcons, gated_canon, gated_all)

The accepted values for
[`novelizer autonomy`](#novelizer-autonomy-level-agent) are the four
members of `AutonomyLevel` (`novelizer/canon/autonomy.py`), lowercase and
exact. Levels are cumulative — each level gates everything the levels
above it in this table gate, plus its own tier:

| Level | Effect on an agent's commits |
|---|---|
| `full_auto` | Commits go directly to canon; only the always-gated event (`blueprint.adopted`) becomes a proposal. This is the default when no level has ever been set. |
| `gated_retcons` | Retcon-tier events additionally become proposals: `world_entry.superseded`, `flag.resolved`. |
| `gated_canon` | Canon-tier events additionally become proposals: `world_entry.created`, `character.created`, `character.updated`, `chapter.created`, `chapter.status_changed`, `chapter.revised`, `secret.revealed`. |
| `gated_all` | Every event type not explicitly never-gated becomes a proposal. |

Two fixed sets sit outside the tiers (`novelizer/canon/policy.py`):

- **Always gated** — `blueprint.adopted` requires approval at every level,
  including `full_auto`.
- **Never gated** — bookkeeping and signal events (director signals, chat,
  thread/secret/promise/arc tracking, planning windows,
  `blueprint.retargeted`, flag escalations, and similar) commit directly at
  every level, including `gated_all`.

The effective level for a commit is the committing agent's override if one
is set, otherwise the global level (`AutonomyState.level_for`); the gating
committer resolves it per commit attempt against the live state. A gated
commit appends a `proposal.created` event instead of the target event —
see [`novelizer proposals`](#novelizer-proposals),
[`approve`](#novelizer-approve-proposal_id), and
[`reject`](#novelizer-reject-proposal_id) for the queue and decisions.

This section states only the accepted values and their effects. Which
event types fall into each tier and why — the reasoning behind the
retcon/canon split and the never-gated set — is explained in
[how-the-room-works.md](../explanation/how-the-room-works.md) (Autonomy
levels and gating tiers); the policy source is `novelizer/canon/policy.py`
(tier resolution via `substrate/policy.py::is_gated`).

## Exit and error behavior summary

The CLI distinguishes three failure planes — environment errors, argument
errors, and domain rejections — and only the first two are visible in the
exit code:

| Situation | Behavior |
|---|---|
| Config/story errors (`TOMLFileError`, `StoryConfigError`, `FileExistsError` caught in the group callback), invalid `--story` path | `click.ClickException`: `Error: <message>` on stderr, no traceback, exit 1 (`tests/director/test_cli.py::test_config_error_shown_as_friendly_message_not_traceback`) |
| Malformed arguments — unknown subcommand, missing required argument, non-integer where `type=int` (`plan-resolution`/`plan-reveal` windows, `retarget` count) | `click` usage error with usage text, exit 2 |
| Domain rejections — unknown chapter/thread/secret/proposal ID, already-decided proposal, invalid window, no active blueprint, unknown autonomy level, scaffold refusals | Colored message on stdout, **exit 0**; no event appended |
| Bad voice pack given to `novelizer voices --pack` — missing file (`FileNotFoundError: Voice pack not found at '<path>'.`) or malformed TOML (`tomllib.TOMLDecodeError`) | Uncaught — traceback, nonzero exit; `voices` has no domain-rejection handling (contrast `voice-scaffold`, which creates a missing pack file and catches a malformed one as a `ValueError`, exit 0) |
| Quitting the setup wizard or story picker on bare invocation | Exit 0, TUI not launched |
| Successful commands | Exit 0 |

Because story resolution and settings loading happen in the `cli` group
callback (`novelizer/director/cli.py`), the exit-1 config/story plane fires
before any subcommand body runs — `novelizer --story /bad/path chapters`
fails on the path, never reaching `chapters`.

**Exit 0 does not mean success.** Every domain rejection prints a message
and returns normally. Scripts must inspect the output text, not the exit
code. Color is not a reliable signal either — it varies by command, not by
outcome:

| Command(s) | Success | Rejection |
|---|---|---|
| `seed` | green | — (no domain rejections; a missing argument is a usage error) |
| `autonomy`, `voice-scaffold` | green | red |
| `read` | title rule + prose | red (not-found) |
| `chapters`, `retcons`, `proposals`, `voices` | table / report (plain empty-state message when there is nothing to list) | — |
| `approve` | green | green (not-found and already-decided too) |
| `reject` | yellow | yellow |
| `plan-resolution`, `plan-reveal`, `retarget` | green (output starts `resolution window` / `reveal window` / `blueprint retargeted`) | yellow (anything else) |

The plan/retarget commands' own success test is a string-prefix check on
the helper's return value — the same check a calling script should use.
The stable machine-checkable contracts are the message texts documented in
each subcommand section above.

## Related documents

- [Tutorial: your first story](../tutorial/first-story.md) — a first
  session end to end: install, wizard, story picker, seeding, and reading
  the first chapter in Mission Control.
- [How-to: connect a local LLM](../how-to/connect-a-local-llm.md) —
  pointing Novelizer at an OpenAI-compatible endpoint (llama.cpp, Ollama,
  vLLM), model picks, token caps, and per-agent tool flags.
- [Quickstart: install to first run](../QUICKSTART.md) — the condensed
  install-to-first-run acceptance walkthrough, covering the checks the
  tutorial doesn't.
- [Configuration reference](configuration.md) — every setting this CLI
  reads and writes, including `default_stories_dir`, `last_opened_story`,
  `suppress_flat_migration_prompt`, `voice_pack`, and `prose_profile`.
- [How the room works](../explanation/how-the-room-works.md) — why
  proposals and the human approval loop exist, and the reasoning behind
  the autonomy gating tiers this reference only enumerates.
- [research-domain CLI](research-domain-cli.md) — the separate
  `research-domain` console script for the `research_domain` bounded
  context (the synthetic proof-domain built on `substrate`); a distinct
  CLI, not part of `novelizer`.
