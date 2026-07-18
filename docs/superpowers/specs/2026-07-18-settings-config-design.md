# Settings & Configuration Design

**Date:** 2026-07-18
**Status:** Approved (brainstormed with Ty)
**Scope:** Layered config files, first-run setup wizard, story picker/creation, in-TUI settings screen.

## Problem

Configuration today is a single flat `Settings` bag (`novelizer/config.py`, pydantic-settings,
`NOVELIZER_` env prefix, `.env` file). `db_path` hardcodes one story. There is no first-run
experience, no UI surface for settings, and the root `.env.example` documents variables that do
not exist. Per-story concerns (voice, cadence, temperatures) leak across stories because there is
no per-story configuration at all.

## Goals

- A layering model that matches how users think: machine-level settings vs. this-novel settings.
- Stories that are portable, self-describing directories.
- A first-run experience that requires no manual file editing.
- A settings screen in the TUI with sane live-apply semantics.
- Full provenance: every generated artifact records the settings it was produced under.

## Non-goals

- Headless/scripted setup (`novelizer init`) — deferred; the wizard core is built TUI-independent
  so this is a cheap follow-on.
- OS keyring integration for secrets.
- Event-sourcing configuration changes (see Provenance).

## 1. Layering model

Four layers, later wins:

```
built-in defaults  ←  global config  ←  story config  ←  env vars (NOVELIZER_*)
```

- **Global config:** `~/.config/novelizer/config.toml` (respect `$XDG_CONFIG_HOME`).
- **Story config:** `story.toml` inside the story directory.
- **Story directory:** a story is a self-contained folder — `world.db`, `chroma/`, `story.toml`.
  Zip it, share it; voice and cadence travel with it.

## 2. What lives where

| Setting | Global | Story | Notes |
|---|---|---|---|
| LLM base URL | ✓ | — | |
| API key | ✓ | **forbidden** | Rejected if present in `story.toml`; redacted in UI |
| Default models (author/agent/embedding) | ✓ | override | A story may pin a model |
| `default_stories_dir` | ✓ | — | Picker scans this |
| Last-opened story | ✓ | — | Written by the app, not the user |
| Voice pack, prose profile | default | ✓ | |
| Temperatures | default | ✓ | |
| Agent cadence intervals | default | ✓ | |
| `db_path`, `chroma_path` | — | — | **Derived** from story dir; no longer settings |

## 3. Loading: `ConfigLoader`

Replaces flat `Settings` with:

- `GlobalConfig` (pydantic) — parsed from global TOML.
- `StoryConfig` (pydantic) — parsed from `story.toml`; secret keys in its forbidden set are
  rejected with a clear error.
- `EffectiveSettings` — immutable merge consumed by `Runtime`. Its field shape stays close to
  today's `Settings` so `runtime.py` and agent builders change minimally.

Behavior:

- Unknown keys **warn**, never crash (forward compatibility for shared stories).
- Invalid TOML → friendly error naming file and line.
- Env vars override everything, mapped by the existing `NOVELIZER_` prefix.

## 4. First-run wizard (in-TUI)

Trigger: global config missing at launch.

Flow: LLM base URL → **live connectivity test** → model picker fed by `GET /v1/models`
(author, agent, embedding selections from an actual list — no guessing model strings) →
optional API key → confirm stories directory → write `config.toml` with mode `0600` →
roll directly into story creation.

The wizard logic is a plain, TUI-independent component (steps as data + an executor), so a
future CLI `init` reuses it.

## 5. Story picker & creation

- Bare `novelizer` → picker: stories found in `default_stories_dir`, most-recent first,
  last-opened preselected; plus **New story**.
- `novelizer <path>` opens a story directory directly.
- **New story:** name → directory slug, voice pack, prose profile → writes `story.toml`,
  initializes `world.db`.
- **Migration:** an existing flat `stories/world.db` triggers a one-time prompt to move it into
  a proper story directory.

## 6. TUI settings screen

Two sections: **Global** and **This story**.

- **Inheritance display:** an unset story field shows the effective global value dimmed with
  "(inherited)". Setting it writes an override into `story.toml`; clearing it removes the key.
- **Write path:** field commit (enter/blur) writes the TOML file. The runtime watches file
  mtime, so hand-edits in an external editor and TUI edits flow through one path — the file is
  the single source of truth.
- **Live-apply:**
  - Cadence intervals → reschedule agents immediately.
  - Voice pack / profile / temperature → picked up on each agent's next generation; UI hints
    "applies to next draft".
  - LLM endpoint / model changes → marked "restart required"; no pretending.
- **Env overrides** are shown as "(overridden by env)" and are non-editable.
- **Test connection** action reuses the wizard's connectivity check.
- API key is rendered redacted.

## 7. Provenance (event-sourcing stance)

Configuration is **not** event-sourced. Instead, provenance is stamped at use-time: generation
events (drafts, revisions) record the model, temperature, voice pack + version, and prose
profile they were produced under. This answers "why does chapter 12 read differently" from the
ledger without polluting it with knob-turns that may never affect anything. The ledger stays
story-facts-only.

## 8. Secrets

- `llm_api_key` lives in the global config file, written `0600` (same trust model as
  `~/.aws/credentials`).
- `NOVELIZER_LLM_API_KEY` env var overrides for users on secret managers.
- Never valid in `story.toml` (stories are shareable): loader rejects it, UI never offers it
  at story scope.

## 9. Error handling

- LLM endpoint unreachable at startup → TUI opens anyway, agents paused, banner offers the
  connection test. Not a crash.
- Story dir missing `story.toml` → treated as all-inherited; offer to create one.
- Invalid TOML / forbidden keys → actionable message naming file, line, and fix.

## 10. Testing

- Red/green TDD per component.
- **Property-based (Hypothesis)** on the merge: for arbitrary layer combinations, precedence
  holds; writing a story override then re-reading round-trips; no merge ever accepts a
  forbidden (secret) key at story scope.
- Wizard and picker cores tested headless (TUI-independent); Textual pilot tests for screens.

## 11. Phasing

1. **Phase 1 — foundation:** `ConfigLoader`, layered files, story-as-directory, derived paths,
   migration of the flat layout. Barely touches agent code, so it lands cleanly while M4 is in
   flight.
2. **Phase 2 — entry UX:** first-run wizard, story picker/creation, connectivity test.
3. **Phase 3 — settings screen:** inheritance display, live-apply, file watching, provenance
   stamping on generation events.

Cleanup: delete the stale root `.env.example`; ship a documented `config.toml` example instead.
