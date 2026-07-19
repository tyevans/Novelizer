# Mission Control Design Pass — Design

**Date:** 2026-07-18
**Status:** awaiting director review
**Feeds into:** M5.3 (UX polish) — this spec is the design input for the polish sweep's TUI portion.
**Grounding:** critique below is taken from live screenshots of the current app rendered
against the real `a-dress-for-doug` story data
([main view](assets/2026-07-18-mission-control-before-main.svg),
[reading view](assets/2026-07-18-mission-control-before-reading.svg)).

## Why

Mission Control works, but it reads like a debug console, not a director's control room.
The vision doc promises "a director's control room for an autonomous writers' room" —
watching six cast personalities build a world should feel like a show. Today it feels
like `tail -f` with borders. This pass redesigns the home screen and its panes for
legibility, hierarchy, and delight, without touching the architecture: every widget
remains a pure reader of projections, and every visual transform is a testable pure
function.

## Inventory of current problems (per pane, from the live screenshots)

1. **No pane is labeled.** Six bordered boxes stack in the left column with no titles.
   A newcomer cannot tell the Thread Board from Who-Knows-What; both render as
   `· Name (id:slug)` lists. (Textual has supported `border_title` since 0.24 — we
   never used it.)
2. **Raw identifiers leak everywhere.** Story Shape prints chapter UUIDs
   (`· 025bae36-6f36-4543-9054-abfcb08921f9  tension=0.30  rising`); Thread Board and
   Who-Knows-What append `(id:the-unraveling-sky)` slugs to every line. The ids exist
   only so the director can type them into `:` commands — they destroy readability for
   the 99% of time spent watching.
3. **Story Shape shows no shape.** The pane named after the tension curve renders
   floats. Three chapters of `tension=0.30 rising` communicates nothing; a sparkline
   would communicate everything at a glance.
4. **The feed renders raw markdown and has no visual identity.** `RichLog` is created
   with `markup=False`, so Editor critiques display literal `**bold**`. Every line is
   the same color and weight: canon events, agent chatter, and filed retcons (the
   alarms!) are visually identical. Long agent notes dump 15+ raw lines and drown the
   room. Source tags render as inline noise (`retcon: [source: voice_drift] clean and
   neutral / no decorative similes violated by-the…`).
5. **Empty panes stay visible as dead bordered boxes.** A quiet story shows
   "no pending proposals", "no causal edges yet", "no secrets yet" — up to five dead
   boxes eating half the left column. On a fresh story (the M5.4 stranger walkthrough's
   first impression) the whole dashboard is empty boxes.
6. **The status bar is a cram.** Roster summary, autonomy level, and a seven-command
   cheatsheet share one dim line. Autonomy — the product's signature control, "the
   dial" — is plain text. The cheatsheet duplicates what a command palette / footer
   should do.
7. **Proposals — the one pane that demands action — has the least visual urgency.**
   When the dial gates, pending proposals are the director's job. Today they render as
   a dim two-line box identical to every other pane.
8. **Room mode ('r') is indistinguishable from the home screen.** It just hides the
   right column; the vision doc's Room — agents speaking in cast personalities with
   rich inline cards — doesn't exist yet. Reading mode ('v') is closer to its promise
   but shares the unstyled detail pane.
9. **The header wastes a row** duplicating the app title, and detail prose renders
   unstyled with no typographic care (no title emphasis, no width limit, no paragraph
   spacing).

## Design principles

1. **Names, not ids.** The director reads titles; machines read slugs. Ids never
   appear on the dashboard. Commands that need targets get selection-driven
   alternatives (act on the highlighted row) and completion, so nobody retypes a slug.
2. **Shape over numbers.** Anything that is a trend or a distribution renders as a
   glyph chart (sparkline, bar, matrix dots), with numbers available in drill-in.
3. **Attention is a color budget.** The default palette is calm and dim. Exactly three
   things earn saturated color: agent identities (fixed hue per agent), canon domain
   accents, and alarms (stale / paradox / leak / drift / pending approval). If
   everything glows, nothing does.
4. **Quiet when empty, loud when it matters.** Empty panes collapse to a single dim
   line (or disappear); the proposals banner appears only when something is pending —
   and then it is the most visible thing on screen.
5. **The room has a cast.** Each agent gets a fixed color + glyph used everywhere
   (feed, roster, room view). Personality is the product; the UI should carry it.
6. **Architecture unchanged.** Widgets stay projection-readers polling `ReadStore`;
   all new rendering is pure `records → Rich renderable` functions, unit-tested
   without a terminal.

## The agent identity system

One source of truth, `novelizer/tui/identity.py`:

| Agent | Glyph | Color (theme variable) |
|---|---|---|
| Author | ✎ | amber |
| Editor | § | violet |
| World Architect | ⌂ | teal |
| Character Keeper | ♥ | rose |
| Continuity Checker | ⚖ | steel blue |
| Retconner | ↺ | orange |
| Structure Analyst | ∿ | green |
| Director (human) | ★ | white/bold |
| System | · | dim |

Glyphs are ASCII-safe-ish single cells with plain-letter fallbacks if the terminal
lacks them. Colors come from Textual theme variables so light/dark terminals both work.

## The redesigned home screen

Three zones instead of a six-box stack: **Feed** (hero, top-left), **Story Brain**
(bottom-left, one tabbed panel), **Browser + Detail** (right). Status bar becomes a
composed strip. Mockup at 120 columns:

```
┌─ THE ROOM ──────────────────────────────────────────────────────┬─ STORY ─────────────────────────────┐
│ ── ch 4 · The Name in the Wind ──────────────────────────────── │ ▸ Chapters (3)                      │
│ ✎ Author     drafted ch 4 — "The Name in the Wind"              │ ▾ Characters (2)                    │
│ ♥ Keeper     Elara learned the name of the sea        ◆ secret  │     Elara                           │
│ § Editor     approved ch 4 — "the closing image lands"          │     The Boy                         │
│ § Editor     ⚠ voice drift: The Boy, ch 4 — filed     [drift]   │ ▸ World (21)                        │
│ ∿ Analyst    scored ch 4 — tension 0.55, rising                 │ ▸ Retcons (2 open) ⚠                │
│ ♥ Keeper     💬 "Elara wouldn't say it that plainly."           │ ▸ Threads (6 · 1 stale)             │
│ ↺ Retconner  resolved: fishbones simile stripped      [drift]   │ ▸ Themes (3)                        │
│                                                                 ├─ THE NAME IN THE WIND ──────────────┤
│ ▼ 2 proposals awaiting approval ── press a ──────────────────── │ ch 4 · approved · 1,840 words       │
├─ STORY BRAIN ──────── 1 Shape · 2 Threads · 3 Secrets · 4 Cause │                                     │
│ tension  ▂▃▅▆▄▇   ch1 ▸ ch6      pacing: rising                 │   The wind came off the water       │
│          ⚠ sag ch5                                              │ carrying the old name, and the      │
│ threads  6 open · 1 stale (The boy's gift — idle 5 ch)          │ boy stood at the rail to hear it…   │
└─────────────────────────────────────────────────────────────────┴─────────────────────────────────────┘
  ✎⠋ §· ⌂· ♥· ⚖· ↺· ∿·    AUTONOMY ▮▮▯▯ gated:canon        :command  ^k        a approve  v read  q quit
```

### Zone 1 — The feed ("THE ROOM")

The heartbeat. Changes:

- `markup=True`; each line built by a pure `render_event(ev) -> Text` (Rich `Text`
  with styles, replacing the current `format_event` string function — same seam, same
  testability).
- **Speaker column**: agent glyph + name in the agent's color, fixed width, so the
  feed scans like a screenplay.
- **Three visual classes of line:**
  - *Canon events* — normal weight, with a dim domain accent chip at line end
    (`◆ secret`, `◆ thread`, `◆ lore`).
  - *Remarks* (`agent.remarked`) — dim italic with 💬; the room chattering.
  - *Alarms* — retcon filings, leaks, paradoxes, voice drift, worker errors — bold
    warning color with ⚠ and a short **source badge** (`[drift]`, `[leak]`,
    `[paradox]`, `[mined]`) parsed from the existing source tags instead of printing
    `[source: voice_drift]` raw.
- **Long payloads clamp to 2 lines** with a dim `… (open in browser →)` suffix; the
  full text is always one selection away in the detail pane. The feed is a pulse, not
  a document.
- **Chapter rules**: when a `chapter.created` event lands, write a dim horizontal rule
  `── ch 4 · The Name in the Wind ──` so the feed self-organizes into acts.
- Markdown from agent notes is stripped/rendered (`**x**` → bold), never shown raw.

### Zone 2 — Story Brain panel

The four brain views merge into **one panel with four tabs** (Textual
`TabbedContent`, keys `1–4`), replacing four stacked always-visible boxes. Each tab
gets a compact "vital" rendering; a persistent one-line summary strip shows the other
tabs' alarm states (e.g. `Threads ⚠1` when a stale thread exists) so nothing is
missed while another tab is open.

- **Shape (1)** — a real sparkline of tension by chapter (Textual `Sparkline`),
  x-axis labeled by chapter number, sag/spike chapters highlighted in the alarm
  color with a one-line callout (`⚠ sag: ch 5 "The Long Calm"`). Chapter *titles* on
  hover/selection — never UUIDs.
- **Threads (2)** — grouped by state: open first, stale pinned to top with
  `⚠ stale — last touched ch 3, 5 chapters ago`, paid-off collapsed to a dim count.
  No slugs; rows are selectable, and selecting a row targets it for commands and
  shows it in the detail pane.
- **Secrets (3)** — the **knowledge matrix** rendered as an actual matrix: rows =
  secrets, columns = character initials, cells `●` known / `○` unknown / `◍`
  suspected, revealed secrets folded to a dim `✓ revealed` group. This is the
  single most "wow"-capable widget in the app — the data (`read.knowledge_matrix()`)
  already exists.
- **Causeway (4)** — `ch 2 "The Gift" ──▶ ch 5 "The Price": note` using chapter
  titles, paradox edges in alarm color with `⚠ PARADOX` and both directions shown.

Empty tabs render one dim line of personality, no box: e.g. Secrets — *"No secrets
yet. The room is still honest."*; Causeway — *"No causal edges yet — nothing has
consequences until the Analyst says so."*

### Zone 3 — Proposals banner

Removed as a resident pane. When `list_proposals(status="open")` is non-empty, a
single-line high-contrast banner slides in between feed and brain panel:
`▼ 2 proposals awaiting approval — press a`. Pressing `a` opens the approval queue
as a modal drill-in (list + full context + approve/reject on the selected row —
no more typing `:approve 025bae36`). When empty: nothing. Zero rows spent.

### Zone 4 — Browser + Detail (right)

- Browser tree keeps its structure but rows gain state cues: retcons section shows
  `⚠` when open items exist; threads section shows the stale count; chapter rows show
  editorial status as a colored dot (`● approved`, `◌ draft`, `◐ revising`) instead of
  `[EditorialStatus.APPROVED]` enum text.
- Detail pane gets typography: title line styled bold in the section's accent color,
  metadata line (status · word count) dim, prose set with a paragraph gap and a
  max content width (~80 cells) for readability. The pane's border title becomes the
  selected item's title (see mockup) so the pane self-labels.
- Reading mode ('v') inherits this typography — it's the same widget.

### Zone 5 — Status strip + command line

- **Roster**: the full cast as glyphs with state marks — `✎⠋` (spinner = running),
  `✎·` idle, `✎‖` paused, `✎!` errored (glyph in agent color; mark carries state).
  Hovering/expanding is unnecessary — errors also land in the feed as alarm lines.
- **Autonomy dial**: rendered as a 4-segment meter + label, `AUTONOMY ▮▮▯▯
  gated:canon`, in a color that steps with trust level (green → amber). It reads as
  a dial, matching the product metaphor.
- **The command cheatsheet leaves the status bar.** `:`/`^k` still focuses the
  command line; the Input's placeholder carries a rotating hint (`:seed a lighthouse
  at the end of the world`), and the footer shows the four real keys (a approve ·
  v read · 1–4 brain · q quit). Full command reference lives in the palette (`^p`)
  and `:help`.

### First-run / empty story

A fresh story currently opens onto five "no X yet" boxes. Instead: feed shows a
welcome block in the Director's voice —

```
★ The room is assembled: Author, Editor, Architect, Keeper, Continuity, Retconner, Analyst.
★ It's quiet. Give them a world:  :seed a lighthouse keeper who taxes the tide
```

Brain panel and browser render their one-line quiet states. First impression becomes
an invitation, not an empty warehouse — this directly serves M5.4's stranger
walkthrough.

## Approaches considered

- **A. Polish-in-place** — keep the six-box stack; add border titles, hide empty
  panes, fix ids→names, sparkline. Cheap, but keeps the flat hierarchy and cram; the
  dashboard still has no focal point. Rejected as the end state, though its items are
  all subsumed by B.
- **B. Three-zone redesign (recommended)** — everything above: feed-as-hero, tabbed
  Story Brain, proposals banner, identity system, dial, empty states. Transforms the
  experience while keeping Textual, the widget seams, and the reader-of-canon
  architecture untouched. Staged to ship in three independently-mergeable phases.
- **C. Full cinematic control room** — B plus The Room drill-in (personality feed
  with expanding inline cards), animated pane transitions, chapter-reader
  typographic mode with pagination. Deferred: The Room deserves its own design pass
  once B's identity system exists to build on (its speaker system is the
  prerequisite), and animation in Textual is polish-after-structure.

## Staging (each phase independently shippable)

1. **Identity & feed** — `identity.py`, `render_event` → styled `Text`, markup on,
   line classes, badges, clamping, chapter rules, welcome block, border titles on all
   panes (one-line fix that alone removes problem #1).
2. **Story Brain panel** — TabbedContent with the four redesigned tabs (sparkline,
   grouped threads, knowledge matrix, titled causeway), alarm summary strip, empty
   states, names-not-ids everywhere.
3. **Command & control** — proposals banner + approval modal, roster glyph strip,
   autonomy dial meter, footer/palette cleanup, detail typography.

## Testing

Follows the established pattern: every visual transform is a pure function
(`render_event`, `shape_tab_model`, `matrix_rows`, `roster_glyphs`, `dial_meter`,
badge parsing) tested red/green without a terminal; property tests where invariants
generalize (e.g. clamping never exceeds 2 lines; matrix rows cover every
secret×character pair; badge parser round-trips every `*_SOURCE_TAG` constant).
Layout and interaction (tab keys, approval modal, banner appearance) via the existing
pilot harness, extending `tests/tui/`. Existing tests asserting current strings
(`test_app_layout`, feed assertions on `app.messages`) are updated in the same phase
that changes their surface — `app.messages` keeps receiving plain-text renderings so
smoke assertions stay string-based.

## Non-goals

- The Room personality drill-in and inline cards (staged after this pass; needs its
  own spec).
- Web UI, mouse-first interaction, user-facing theming beyond the one polished
  default palette.
- New event types, projections, or read-model changes — with one exception: if the
  detail pane's word count needs it, it's computed from prose at render time, not
  stored.
- Settings screen / setup wizard / story picker restyling (separate, smaller pass;
  they should adopt `identity.py` colors when touched).
