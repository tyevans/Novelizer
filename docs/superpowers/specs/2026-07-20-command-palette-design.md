# Unified Command Palette — Design

## Problem

Novelizer's TUI (`novelizer/tui/app.py`, Textual) currently exposes commands two
disconnected ways:

- A colon-command bar: an always-visible `Input(id="command")`, focused via
  `Ctrl+K`, submitted to `commands.dispatch(runtime, line)` in
  `novelizer/director/commands.py`. Dispatch is a hardcoded if/elif chain
  keyed on the first token, covering `seed`, `focus`, `pause`, `resume`,
  `autonomy`, `retarget`, `approve`, `reject`, `muse` (with a `reroll`
  sub-action).
- `App.BINDINGS`: Textual's standard declarative keybinding list, covering a
  different set of actions — approve (`a`), panel toggles (`r`/`e`/`p`/`v`),
  brain tabs (`1`-`6`), quit (`q`) — implemented as separate `action_*`
  methods with no relationship to `commands.dispatch`.

Discoverability for the colon-bar comes only from `PLACEHOLDER_HINTS` in
`novelizer/tui/widgets/roster.py`, a rotating set of example strings shown as
placeholder text — not a searchable list, and it drifts out of sync with
what `dispatch` actually supports.

This means: (a) the command set is split across two disconnected
mechanisms, (b) adding a command means adding another `if` branch, with no
guarantee it's reachable from both keybinding and colon-bar, and (c) there's
no way to discover what commands exist beyond memorizing them or reading
source.

## Goals

- One source of truth for every command (colon-command and keybinding
  alike).
- A single, fuzzy-searchable entry point for discovering and running any
  command.
- Adding a new command requires touching one place, and it's automatically
  reachable from both keyboard shortcut (if bound) and the palette.

## Non-goals

- Changing the semantics or behavior of any existing command (seed, focus,
  pause, resume, autonomy, retarget, approve, reject, muse).
- Building custom fuzzy-matching/filtering UI — Textual's built-in
  `CommandPalette` provides this.
- Multi-key chorded bindings or a keybinding customization system.

## Design

### 1. Command registry

`novelizer/director/commands.py` is refactored around a declarative
registry instead of the if/elif chain in `dispatch()`. Each entry is a
`Command`:

```python
@dataclass
class Command:
    name: str                      # e.g. "seed"
    description: str               # shown in the palette
    callback: Callable[[Runtime, str], str]  # takes (runtime, raw_args) -> result text
    takes_args: bool = False       # True if this command needs a follow-up input
```

`COMMAND_REGISTRY: list[Command]` holds one entry per existing colon-command
(`seed`, `focus`, `pause`, `resume`, `autonomy`, `retarget`, `approve`,
`reject`, `muse`) plus one entry per existing keybound action (approve —
already listed, panel toggles for roster/events/plot/vitals, brain tabs 1–6,
quit). Each callback wraps the existing logic currently inline in the
if/elif branches or `action_*` methods — logic itself does not change,
only where it lives.

`dispatch(runtime, line)` becomes a thin lookup: split the first token,
find the matching `Command` by name (supporting the existing optional
leading `:`), call its callback with the remainder of the line as args.
Unknown command behavior (`"Unknown command: ..."`) is preserved.

### 2. Keybindings become registry-backed

`App.BINDINGS` keeps its existing key list (`a`, `r`, `e`, `p`, `v`, `1`-`6`,
`q`, plus `ctrl+k`), but each bound `action_*` method becomes a one-line
wrapper that looks up the corresponding `Command` by name in
`COMMAND_REGISTRY` and invokes its callback. This guarantees a keypress and
a palette selection of the same command run identical code — no drift
possible between the two entry points, since there's only one
implementation per command.

### 3. Palette UI replaces the colon-bar

The always-visible `Input(id="command")` widget and `PLACEHOLDER_HINTS`
rotation are removed. In their place:

- `Ctrl+K` (existing key, kept for muscle memory rather than switching to
  Textual's `Ctrl+P` default) opens Textual's native command palette
  (`textual.command.CommandPalette` / `Provider` pattern).
- `NovelizerCommandProvider(Provider)` is implemented, yielding `Hit`
  objects fuzzy-matched against `COMMAND_REGISTRY` entries' `name` +
  `description`, using Textual's built-in `matcher` for scoring/highlight.
- Selecting a `takes_args=False` command runs its callback immediately and
  closes the palette.
- Selecting a `takes_args=True` command (`seed`, `retarget`, `focus`, `muse`
  — anything whose current syntax takes a value after the command word)
  closes the palette and opens a single-line follow-up `Input`, pre-filled
  with `"<command> "` and cursor at the end, for the user to complete (e.g.
  typing `42` after `seed `). Submitting runs `dispatch(runtime, line)` as
  today.

This follow-up input is the *only* remaining use of a raw text `Input` for
commands — it's scoped to the one selected command, not a permanently
visible general-purpose bar.

### 4. Discoverability

`PLACEHOLDER_HINTS` is deleted. The `description` field on each `Command`
is what the palette displays and searches over, so every command is always
visible and current by construction — there's no separate hint list to
fall out of sync.

## Data flow

```
Keypress (bound key)          Ctrl+K → palette → select "Approve"
        |                                    |
        v                                    v
  action_* wrapper  ---------->  COMMAND_REGISTRY lookup by name
                                             |
                                             v
                                    Command.callback(runtime, args)
                                             |
                                             v
                                  result text -> status/log area
```

For args-taking commands, the palette path additionally routes through the
follow-up `Input` before reaching `dispatch`/`callback`, matching today's
colon-bar flow exactly.

## Error handling

- Unknown command name reaching `dispatch` (shouldn't normally happen since
  the palette only offers registered names, but the follow-up `Input` is
  free text after the command word): preserve existing `"Unknown command:
  ..."` behavior for consistency, though in practice only the args portion
  is user-typed now.
- Malformed/missing arguments for an args-taking command: unchanged —
  existing per-command validation inside each callback (carried over
  verbatim from today's if/elif bodies) continues to apply.

## Testing

- Unit tests for `COMMAND_REGISTRY` completeness: every name referenced by
  an `App.BINDINGS` entry must resolve to a registry entry (prevents silent
  drift going forward).
- Unit tests for `dispatch()` against the new registry-backed
  implementation, covering all 9 existing colon-commands with the same
  cases currently covered (if existing tests exist for `commands.dispatch`,
  they should continue to pass unmodified against the new implementation).
- A test asserting `NovelizerCommandProvider` yields a `Hit` for every
  `COMMAND_REGISTRY` entry given an empty query (full listing), and that
  fuzzy queries narrow the set as expected for a couple of representative
  substrings.
- Per project convention: do not run the test suite in the main checkout
  (prior DB-lock incident) — run in an isolated worktree.

## Migration notes

- This is an internal refactor of an existing TUI surface with no
  persisted state or external API — no data migration needed.
- The colon-bar's `Input(id="command")` widget ID and any code referencing
  it directly (if any exists outside `app.py`/`commands.py`) should be
  grep-checked before removal.
