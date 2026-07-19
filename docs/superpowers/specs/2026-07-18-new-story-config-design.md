# New-Story Config Form — Design

Date: 2026-07-18
Status: Approved

## Problem

Creating a story today captures only a title: `StoryPickerApp` shows a lone
name `Input`, slugifies it, and `create_story()` writes `story.toml` with just
`{"title": ...}`. Voice pack and prose profile — already story-overridable
settings — can only be changed after creation via the Settings screen, and
there is no way to give the story an opening premise; the director has to type
`:seed` after the app boots.

## Decision summary

- **Premise = initial seed.** The premise text becomes one
  `director_signal.created` event (kind=`seed`) appended at creation time —
  identical to typing `:seed <text>` immediately after opening the story. No
  persistent "premise" field, no new domain concepts.
- **Fields:** Title, Premise (optional), Voice pack, Prose profile. Nothing
  else (models, temperatures, intervals stay in the Settings screen).
- **Flow:** a single inline form inside the story picker, not a multi-step
  wizard or a separate optional screen.

## UX

In `StoryPickerApp` (novelizer/tui/story_picker.py), selecting "➕ New story"
reveals an inline form where the lone name input sits today:

```
┌─ Novelizer ──────────────────────┐
│  ➤ ➕ New story                   │
│ ┌─ New story ────────────────┐   │
│ │ Title:   [Iron Harvest____]│   │
│ │ Premise: [A tired thief    ]│  │
│ │          [takes one last...]│  │
│ │ Voice pack: [default     ▾]│   │
│ │ Profile:    [plain       ▾]│   │
│ │   [ Create ]   [ Cancel ]  │   │
│ └────────────────────────────┘   │
└──────────────────────────────────┘
```

- **Title**: `Input`, required. Slug/duplicate validation unchanged (name
  required; `stories_dir/<slug>` must not exist; errors shown in
  `#picker_error`).
- **Premise**: small `TextArea` (~3 lines), optional. Empty ⇒ no seed event.
- **Voice pack**: `Select` populated by discovery (below). Defaults to the
  effective (global) `voice_pack`.
- **Prose profile**: `Select` populated from the chosen pack's
  `prose_profiles`; repopulates on pack change (`Select.Changed`). Defaults to
  the effective `prose_profile` when that profile exists in the chosen pack,
  else the pack's first profile.
- **Create** button (and Enter from the title field) submits; **Cancel** or
  Esc collapses the form back to the option list.
- Widget patterns follow `setup_wizard.py` (Selects populated at runtime,
  `_finish()`-style collection).

Creating with everything left at defaults behaves exactly like today's flow.

## Voice pack discovery

New helper in the voices layer (`novelizer/voices/discovery.py`):

```python
def discover_voice_packs(stories_root: Path) -> list[tuple[str, str]]:
    """(label, path) pairs: the shipped default pack, plus any *.toml files
    directly under stories_root (the `voice-scaffold` convention —
    its default output is stories/user_pack.toml)."""
```

- Shipped pack labeled by its pack `name`; user packs labeled by pack `name`
  (fallback: file stem) with the file name for disambiguation.
- Only files directly under the stories root — story directories' `story.toml`
  files are inside subdirectories and are never picked up.
- Unparseable pack files are skipped (not fatal to the picker).
- Profiles load via the existing `load_voice_pack()`.

## Persistence

`create_story()` (novelizer/settings/story_dir.py) gains an optional
overrides parameter:

```python
def create_story(root, title, overrides: dict[str, str] | None = None) -> StoryDirectory
```

- Keys are validated against the existing `STORY_OVERRIDABLE_KEYS`; unknown or
  forbidden keys raise (reusing the story-config validation behavior).
- The picker passes `voice_pack` / `prose_profile` **only when the chosen
  value differs from the inherited effective value**. Rationale: `story.toml`
  is shareable; unconditionally writing the shipped pack's absolute
  site-packages path would bake a machine-specific path into every story.

## Seed injection

If premise text is non-empty, after `create_story()` returns the picker:

1. constructs `EventStore(StoryDirectory(root).db_path)` and `await init()`
   (EventStore is standalone — creates its own schema; no Runtime needed),
2. appends via the existing `commands.seed(events, text)` →
   `director_signal.created` with `SignalKind.seed`,
3. closes the store, then exits with the story root as today.

The projector picks the event up on first open like any other log entry.
Failure to append (e.g. disk error) surfaces in `#picker_error` and does not
delete the already-created story directory.

## Out of scope / unchanged

- Headless CLI auto-create of the `"default"` story (`_resolve_story`).
- Settings screen, agents, read model, event schema (no new event types).
- Per-chapter voice overrides, "story bible" premise fields.

## Testing

Red/green TDD throughout:

- **Unit**: `create_story` with overrides (valid keys written, unknown/
  forbidden keys rejected, no overrides ⇒ byte-identical behavior to today);
  `discover_voice_packs` (shipped-only, with user packs, skips unparseable);
  seed-at-birth appends exactly one `director_signal.created` with the premise
  text (read back via EventStore).
- **TUI pilot tests** (existing picker test style): create with defaults ⇒
  story.toml has only title, no seed event; create with premise + non-default
  voice ⇒ overrides in story.toml + seed event present; profile Select
  repopulates on pack change; Cancel/Esc collapses form; duplicate-name and
  empty-name errors still shown.
