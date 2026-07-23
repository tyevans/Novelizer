# Setup wizard: visible labels and inline help

**Date:** 2026-07-22
**Problem:** Every field in the first-run setup wizard (`novelizer/tui/setup_wizard.py`)
is labelled only by its `placeholder` (Inputs) or `prompt` (Selects). Fields that ship
with a default value (`base_url`, `stories_dir`) never show their placeholder, so the
user sees `http://localhost:8080/v1` and `stories` with no indication of what they are
setting. The model Selects lose their prompt as soon as the probe assigns a value.

## Decision

Always-visible label above each field plus a dim one-line help text beneath it
(approach chosen over a focus-tracked contextual help panel — a first-run wizard
should be fully readable at a glance, and the form already lives in a
`VerticalScroll` so vertical space is cheap).

## Field copy

| Field | Label | Help |
|---|---|---|
| `base_url` | LLM base URL | OpenAI-compatible endpoint (llama.cpp, vLLM, Ollama, LM Studio, OpenRouter…). Include the `/v1` suffix. |
| `api_key` | API key | Sent as a Bearer token. Leave blank for local endpoints that don't need auth. |
| `stories_dir` | Stories directory | Where your stories live. `~` expands; relative paths resolve from where you launch novelizer. |
| `author_model` | Author model | Writes the prose — pick your strongest model. |
| `agent_model` | Agent model | Runs the support agents (editor, continuity, plotting…). A faster model works well. |
| `embed_model` | Embedding model | Builds the semantic index used for canon search. Must be an embedding model. |

Copy is verified against the actual semantics: `stories_dir` goes through
`Path(...).expanduser()` in `director/cli.py`; the probe sends
`Authorization: Bearer <key>` in `settings/setup_core.py`.

## Mechanics

- Compose helper yields `Label(…, classes="field-label")`, the widget, and
  `Static(…, classes="field-help")` for each field.
- Redundant placeholders/prompts are dropped where the label + help now carry the
  information; disabled Selects keep a "run Test connection first" prompt because
  that is state, not labelling.
- Widget IDs, probe/save/skip behavior, and `setup_core` are untouched.
- CSS: `.field-help` renders dim with bottom margin; per-widget `margin-bottom`
  moves to the help line.

## Testing

- New: labels render for every field even when a default value is present
  (the original bug); help lines render with the expected copy.
- Existing five tests in `tests/tui/test_setup_wizard.py` must pass unchanged
  (they address widgets by ID).

## Out of scope

The settings screen (`settings_screen.py`) — it is a labelled DataTable and does
not share the placeholder-as-label problem.
