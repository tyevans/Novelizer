# Generic issue-flagging (Flag model + Triage agent)

## Problem

Agents routinely notice problems that don't fit their own structured intent
types and currently have nowhere to put them but a free-text `feed_note`,
which nobody acts on. Two overlapping gaps:

1. Structure Analyst, Plotter, and World Architect have no way to file a
   structured issue at all (only `feed_note`).
2. The one flagging mechanism that does exist, `RetconRequest`, is wired
   only for contradictions and only into Continuity Checker, Character
   Keeper, and Editor (`voice_drift_flags`).

## Data model

Replace `RetconRequest` / `RetconStatus` (`store/models.py`) with a generic
`Flag`:

- `id`
- `category: str` — free-form (`"contradiction"`, `"pacing"`, `"thematic"`,
  `"worldbuilding"`, or anything an agent invents)
- `description`
- `related_entry_ids: list[str]` (renamed from `conflicting_entry_ids`)
- `proposed_resolution: str`
- `status`: `open` / `resolved` / `rejected` / `stale`
- `filed_by`, `resolved_by`
- `triage_passes: int = 0` — incremented each catch-all pass that leaves an
  unowned flag unresolved; past a threshold N it flips to `stale`.

New events `FLAG_CREATED` / `FLAG_RESOLVED` / `FLAG_REJECTED`. The projector
keeps handling the legacy `RETCON_REQUEST_CREATED/RESOLVED/REJECTED` event
types as an alias that projects into `category="contradiction"` Flags, so
existing event logs replay unchanged — this is an event-sourced store,
history is never rewritten.

## Filing

Every agent's structured output schema gets one generic field:

```
flags: list[FlagDraft]   # category, description, related_entry_ids, proposed_resolution
```

This replaces the ad-hoc `retcon_requests` field (Continuity Checker,
Character Keeper) and `voice_drift_flags` (Editor), and is newly added to
Structure Analyst, Plotter, and World Architect's output schemas. Commit
path is identical everywhere: each `FlagDraft` becomes a `Flag`, committed
via `FLAG_CREATED`.

## Triage agent (new)

Polls all open flags regardless of category, same poll/readiness shape as
existing agents. For each flag:

1. **Dedup** against other open flags describing the same underlying issue.
2. **Verify** it's real — cite evidence, same verify-before-act discipline
   Retconner already applies to contradictions (the report may be stale by
   the time anyone looks at it).
3. **Route**: a small code-level `category -> owner agent` map (e.g.
   `contradiction: retconner`, `pacing: structure_analyst`,
   `worldbuilding: world_architect`, `thematic: plotter`; extensible)
   tells Triage whether the category has an owner.
   - **Owned + verified**: leave the flag open, untouched. The owning
     agent picks it up on its own next poll via
     `list_flags(category=..., status=open)` — exactly how Retconner
     already polls `list_retcon_requests(open)` today, just generalized.
   - **Verified but dismissed as not real**: `FLAG_REJECTED`.
   - **No owner mapped (catch-all)**: Triage either reclassifies into a
     known category, resolves/dismisses it directly if trivial, or
     increments `triage_passes`. After N passes with no resolution, the
     flag is auto-marked `stale` so it stops looping forever and instead
     surfaces as "needs a human."

Retconner is refactored to consume `Flag(category="contradiction")` instead
of `RetconRequest`; its verify-then-amend logic is unchanged. The
`gated_retcons` autonomy level (`canon/autonomy.py`) gates by *agent name*,
not request type, so it continues to work unchanged.

## TUI

- Browser widget's "Retcons" section (`tui/widgets/browser_model.py`)
  generalizes to a "Flags" section: grouped by category, each with its own
  count, backed by `list_flags(category=..., status=...)` in place of
  `list_retcon_requests`.
- The ⚠ badge changes meaning: today it fires on any open retcon; going
  forward it fires on `stale` flags specifically, since plain `open` flags
  are expected to be actively worked by an owner or by Triage's catch-all.
  A pile of merely-`open` flags is normal; a `stale` one means the system
  gave up and a human should look.
- No other TUI surface (roster, engine room, feed) needs new concepts —
  they already show agent/category activity generically once `list_flags`
  replaces `list_retcon_requests` as the read path.

## Testing

- Unit/property tests for dedup logic and category-routing (owned vs.
  catch-all).
- Round-trip integration test: agent files a flag -> Triage verifies ->
  owner agent resolves.
- Projector tests confirming legacy `RETCON_REQUEST_*` events still replay
  correctly as `category="contradiction"` Flags.
- `triage_passes` / stale-threshold test: an unowned flag that survives N
  catch-all passes without resolution ends up `stale`, not looping forever.
