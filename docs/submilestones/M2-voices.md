# M2 · Voices — Sub-Milestone Breakdown

M2 makes voice a first-class, configurable material: the prose voice the Author writes in,
the personalities the agents work and *speak* in, and the per-character voices the system
builds and enforces. Like M1, it's decomposed into just-in-time sub-milestones, each
independently shippable and testable, planned one at a time and executed via
subagent-driven development (spec-informed plan → fresh-subagent-per-task → two-stage
review → whole-branch review → merge).

Parent milestone in [`../MILESTONES.md`](../MILESTONES.md); end-state design (voicing
system, The Room) in
[`../superpowers/specs/2026-07-17-novelizer-vision-design.md`](../superpowers/specs/2026-07-17-novelizer-vision-design.md).

## Sub-milestones

| # | Name | Delivers | Done when | Status |
|---|------|----------|-----------|--------|
| M2.1 | **Voice packs & prose profiles** | TOML voice-pack format + loader (pydantic models) + a shipped default pack; config for the active pack + active prose profile; the active prose profile's natural-language casting note injected into the Author's work-time prompt and referenced by the Editor's enforcement; CLI/command to list packs and switch the active prose profile | Switching the prose profile changes the Author's next-chapter prompt (the profile's casting note is present in the work input; profile A vs B produces different prompts) | ✅ complete |
| M2.2 | **Personalities & the living feed** | Per-agent personality casting notes (from the pack) injected into each agent's work prompt; agents emit a short in-personality `feed_note` in their structured output → an `agent.remarked` event (never gated) → feed + The Room view render personality-voiced lines | Recasting an agent's personality visibly changes what it says in the feed | ✅ complete |
| M2.3 | **Character voices & voice browser** | CharacterKeeper builds/updates a per-character voice card (dialogue patterns, vocabulary, tics); Editor cites it; card shown in the story browser. In-TUI voice-pack browser (packs / prose profiles / agent personalities / character voices) + scaffolding a new profile from a one-line prompt | Characters accrue voice cards browsable in the TUI; you can scaffold a new voice profile from the TUI | ✅ complete |

## Load-bearing design decisions

- **Voice lives in files, injected at work-time.** Voice packs are human-editable TOML
  ("casting notes" — natural language, not parameter soup), loaded into pydantic models.
  The active prose profile / agent personality is injected into the agent's **work-time
  prompt** (the user message built in `work()`), NOT baked into the deepagents
  `system_prompt` at construction — so switching a profile/personality takes effect on the
  next run without rebuilding agents, and per-chapter overrides remain possible.
- **The Runtime is the voice source.** Runtime loads the active pack from settings and
  hands each agent its personality casting note (and the Author/Editor the active prose
  profile). Agents gain a small additive voice parameter; the existing
  `Committer`/`ReadStore`/scheduler seams are untouched.
- **Personality reaches the feed via canon.** In-personality remarks are `agent.remarked`
  events (never gated — flavor, like director signals), so the living feed is itself
  event-sourced and auditable, consistent with the rest of the system.

## Standing principles (unchanged)

Event sourcing (log sole truth; only Projector writes projections; state changes via
appended events), DDD bounded contexts, SOLID (voice injected via a small provider the
agents depend on; extension over modification), red/green TDD black-box-first with
property/parametrized tests, spec + code review as gates.
