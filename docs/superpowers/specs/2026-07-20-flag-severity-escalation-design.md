# Flag severity + escalation

## Problem

The generic issue-flagging system (`docs/superpowers/specs/2026-07-20-generic-issue-flags-design.md`)
gives every flag a uniform lifecycle: filed, triaged, routed to an owning
agent, resolved or rejected. There is no way to distinguish a critical issue
(contradicts a resolved arc, breaks a paid-off thread, spans multiple
already-written chapters) from a minor one — both flow through Triage and
the owning agent identically. When an owning agent declines or fails to fix
something, the flag is simply left `open` for the next poll; nothing
surfaces that it's a problem worth a human's attention, however severe. A
critical issue can sit invisibly alongside routine ones indefinitely.

This spec adds severity classification and an escalation path. It does
**not** add a multi-step "Repair Planner" for coordinated cross-entry
amendments — that remains a follow-on, out of scope here. This spec only
ensures critical/repeatedly-failing issues become visible and reviewable.

## Data model

Extend `Flag` (`store/models.py`):

- `severity: Literal["minor", "major", "critical"] | None` — null until
  Triage assesses it (matches how `category` can start unowned).
- `escalated: bool = False` — cheap-to-query denormalization of "does an
  unresolved `FLAG_ESCALATED` currently apply to this flag." Kept in sync by
  the events below rather than computed at read time, consistent with how
  `status` already works.
- `failed_attempts: int = 0` — incremented whenever an owning agent's poll
  cycle declines or fails to resolve the flag (mirrors the existing
  `triage_passes` counter pattern, but tracks owning-agent attempts, not
  catch-all triage passes).

New events, alongside the existing `FLAG_CREATED` / `FLAG_RESOLVED` /
`FLAG_REJECTED`:

- `FLAG_ESCALATED` — `flag_id`, `severity`, `reason` (`"triage_critical"` |
  `"repeated_failure"`), `triggered_by`.
- `FLAG_ESCALATION_CLEARED` — `flag_id`, `cleared_by` (`"agent"` |
  `"human"`), `note: str | None`.

Both are append-only facts, consistent with the rest of the event-sourced
canon — escalation state is derived by replay, never rewritten.

## Triage agent changes (`agents/triage.py`)

`TriageVerdict` schema gains `severity: Literal["minor","major","critical"]`,
assessed alongside the existing `real`/`dismiss` verdict.

On a `real` verdict:

1. Set `Flag.severity` from the verdict.
2. If `severity == "critical"`, commit `FLAG_ESCALATED(reason="triage_critical")`
   in the same commit as the flag update, then route to the owning agent as
   normal (escalation does not skip the normal fix attempt — see Escalation
   semantics below).
3. Route to owning agent per the existing category map, unchanged.

Independent of Triage's per-flag pass: each owning agent's decline/fail path
(the place that currently just leaves a flag `open` after a failed attempt)
increments `failed_attempts`. When `failed_attempts` crosses a threshold of
3, that same commit also emits `FLAG_ESCALATED(reason="repeated_failure")` —
this applies regardless of the flag's original severity, so a `minor` flag
that repeatedly resists fixing still surfaces for review.

## Escalation semantics

Escalation is a visibility signal, not a routing gate. An escalated flag
still goes through the same owning-agent poll/attempt cycle as any other
open flag — the intent is "don't silently let critical or stubborn issues
sit unnoticed," not "stop automated repair and wait for a human." A human
can intervene at any time via the review screen below, but nothing requires
them to before automated resolution proceeds.

## Auto-clear on resolution

Wherever `FLAG_RESOLVED` is committed today (owning agents' resolve paths),
if the flag's `escalated` field is true, the same transaction also commits
`FLAG_ESCALATION_CLEARED(cleared_by="agent")`. This is the common case: the
owning agent eventually succeeds and the escalation resolves itself with no
human involvement.

## TUI: Escalations review screen

New `tui/escalations_screen.py` (`Screen`, following the `settings_screen.py`
convention: `BINDINGS` includes `("escape", "dismiss_screen", "Back")`,
dependencies injected via `__init__`, UI built in `compose()`).

Backed by a new pure `escalations_model.py` following the `browser_model.py`
seam (plain functions taking `read: ReadStore`, no Textual imports):

- List pane: `read.list_flags(escalated=True)` — small addition to
  `ReadStore` (`canon/read_store.py`), mirroring the existing
  `list_flags(category=..., status=...)` signature.
- Detail pane, per selected flag: a readable timeline projected from
  `EventStore.events_for_aggregate(flag_id)` (`canon/event_store.py:123`) —
  created → triage verdict/severity → escalated → owning-agent attempts/
  declines → cleared (if applicable). This is new projection logic (no
  existing helper turns raw events into a flag timeline), living in
  `escalations_model.py`.
- Related canon entries (`Flag.related_entry_ids`) rendered read-only
  alongside the timeline, so the reviewer has enough context to judge the
  issue without leaving the screen.
- A "Clear" action, with an optional free-text note, commits
  `FLAG_ESCALATION_CLEARED(cleared_by="human", note=...)`. This does not
  resolve or reject the underlying flag — it only clears the escalation
  (e.g. "I've decided this inconsistency is acceptable" or "I fixed it by
  hand outside the system"). Clearing the escalation while the flag stays
  `open` is a valid, expected state.

Registered the same way as `talk_to_project` (`tui/app.py`): a `BINDINGS`
entry + an `AppCommand` appended to `APP_COMMANDS` for command-palette
access.

## Out of scope

- Repair Planner / coordinated multi-entry amendment sequencing.
- Any change to `TriageVerdict`'s existing `real`/`dismiss` logic beyond
  adding `severity`.
- Notifications/alerts outside the TUI (e.g. push notification on
  escalation) — the review screen is pull-based for this iteration.

## Testing

- Unit test: Triage assigning `critical` severity commits `FLAG_ESCALATED`
  in the same transaction as the flag update.
- Unit test: an owning agent's decline path increments `failed_attempts`
  and crosses the threshold correctly (off-by-one at exactly 3).
- Integration test: full round trip — flag filed, Triage marks critical,
  `FLAG_ESCALATED` fires, owning agent later resolves it, `FLAG_RESOLVED`
  and `FLAG_ESCALATION_CLEARED(cleared_by="agent")` both land.
- Integration test: repeated-failure path escalates a `minor` flag purely
  from `failed_attempts`, independent of Triage's original severity call.
- Projector tests: `escalated` field on `Flag` reflects the latest
  `FLAG_ESCALATED`/`FLAG_ESCALATION_CLEARED` pair correctly on replay.
- TUI: `escalations_model.py` functions tested directly against a
  `ReadStore` fixture (no Textual harness needed, per existing `*_model.py`
  convention).

**Status:** design approved, not yet implemented.
