# Configuration reference

Novelizer settings are defined in `novelizer/settings/` (`models.py`, `layers.py`, `loader.py`, `view_model.py`). This page lists every setting, where it may be set, its default, and how changes are applied. For a task-oriented walkthrough of pointing Novelizer at an LLM endpoint, see [Connect a local LLM](../how-to/connect-a-local-llm.md).

## Configuration layers and precedence

Effective settings (`EffectiveSettings`, an immutable model) are a pure merge of four layers, lowest precedence first (`build_effective` in `novelizer/settings/loader.py`):

1. **Built-in defaults** — the field defaults on `EffectiveSettings` (`novelizer/settings/models.py`).
2. **Global config file** — `config.toml` (see locations below). All fields optional; unset means "fall through to defaults".
3. **Story config file** — `story.toml` inside the story directory. Only story-overridable keys are accepted (see key scoping).
4. **Environment variables** — `NOVELIZER_*` (including implicit `.env` pickup — see locations below), the highest-precedence layer.

A key set in a higher layer wins outright; a key left unset in a layer falls through to the layer below. Two exceptions to the merge:

- `db_path` and `chroma_path` are accepted by no layer. When a story is open they are derived from the story directory; otherwise they hold their built-in defaults.
- `story_title` comes only from the `title` key in `story.toml` — the global config and environment cannot set it.

The Settings screen (command palette entry `settings` in the TUI) shows each row's winning source (`default` / `global` / `story` / `env`). Rows whose value comes from an environment variable are read-only there — clear the variable to edit them again.

## File and environment variable locations

| Layer | Location |
|---|---|
| Global config | `$XDG_CONFIG_HOME/novelizer/config.toml`, or `~/.config/novelizer/config.toml` when `XDG_CONFIG_HOME` is unset or empty (`global_config_path()` in `novelizer/settings/layers.py`) |
| Story config | `<story-directory>/story.toml` |
| Environment | `NOVELIZER_<KEY>` — the key name uppercased with the `NOVELIZER_` prefix, e.g. `NOVELIZER_LLM_BASE_URL`, `NOVELIZER_AUTHOR_MODEL` |
| `.env` file | A `.env` in the current working directory is picked up implicitly (legacy back-compat with the pre-layers `Settings` behavior), using the same `NOVELIZER_` prefix. A variable set in the real environment beats the same variable in `.env` |

Neither file needs to exist: a missing `config.toml` or `story.toml` simply contributes nothing to the merge, and every key falls through to the layer below.

Novelizer always writes the global config file with mode `0600` (`novelizer/settings/global_store.py`), because it may hold `llm_api_key`. Unrecognized `NOVELIZER_*` environment variables are ignored silently (`extra="ignore"` on `EnvOverrides` in `novelizer/settings/loader.py`) — unlike unknown keys in the config files, they produce no warning.

While the TUI runs with a story open, the current story's `story.toml` and the global `config.toml` are both watched via a 1-second mtime poll (`SETTINGS_POLL_INTERVAL` in `novelizer/tui/app.py`). Edits to either file — from the Settings screen or an external editor — are re-loaded, merged, and applied to the running system through a single path (`Runtime.apply_settings`). The watcher tracks only those two TOML files — editing `.env` does not trigger a reload on its own.

## Key scoping: story-overridable, global-only, and forbidden keys

Every key falls into one of four scopes:

- **Story-overridable** (`STORY_OVERRIDABLE_KEYS` in `novelizer/settings/models.py`) — may appear in `story.toml`, the global config, or the environment. Covers voice (`voice_pack`, `prose_profile`), models (`author_model`, `agent_model`, `embed_model`), temperatures, all configurable agent cadence intervals, context/analysis thresholds, Muse settings, and every per-agent tool/subagent flag except the triage ones (see the [field reference](#field-reference) tables for the per-key scope column). The Settings screen writes edits to these keys into the current story's `story.toml`; submitting an empty value removes the key so the story inherits from the global config or defaults again.
- **Global-only** — accepted in the global config and the environment, but not in `story.toml`, where they are ignored with a logged warning (`unknown or global-only setting ... ignored`): `llm_base_url`, `llm_max_tokens`, `embed_base_url`, `default_stories_dir`, `last_opened_story`, `suppress_flat_migration_prompt`, `max_concurrent_agents`, `outline_gate_enabled`. For those that appear as Settings-screen rows (`last_opened_story` and `suppress_flat_migration_prompt` are [hidden](#secret-and-hidden-keys)), the Settings screen writes edits into the global config file.
- **Forbidden in `story.toml`** (`FORBIDDEN_STORY_KEYS` in `novelizer/settings/models.py`) — `llm_api_key` and `embed_api_key`. Their presence in a `story.toml` raises `StoryConfigError` at load time rather than a warning, because story directories are meant to be shareable and must never carry secrets. It is accepted in the global config and the environment.
- **Fixed at their defaults** — `triage_interval`, `triage_tools_enabled`, and `triage_subagent_enabled` exist on `EffectiveSettings` but appear in no configuration layer (not even the environment); they always hold their built-in defaults. `db_path` and `chroma_path` are likewise accepted by no layer — they are derived from the story directory (see precedence above).

One key runs the other direction: `title` is accepted only in `story.toml` (it surfaces as `story_title` in effective settings) — the global config and environment cannot set it.

`create_story(...)` (`novelizer/settings/story_dir.py`) validates any initial overrides against `STORY_OVERRIDABLE_KEYS` before creating the directory and raises `StoryConfigError` (`... are not story-overridable settings`) for anything else, so a bad call leaves no half-created story.

## Settings that require a restart (RESTART_REQUIRED_KEYS)

`RESTART_REQUIRED_KEYS` (`novelizer/settings/view_model.py`) is the set of keys whose new values only take effect on the next launch:

```
llm_base_url, llm_api_key, llm_max_tokens, author_model, agent_model, embed_model,
embed_base_url, embed_api_key
```

These are the endpoint, credential, and model keys — everything wired into agent runners and the embedding index at construction time. Every other key applies live to the running system.

When a reloaded configuration changes one of these keys, `Runtime.apply_settings` (`novelizer/runtime.py`) deliberately leaves it un-applied: the runtime's stored settings keep the *old* value for the restart-required keys, so `Runtime.settings` always reflects what is actually running, while the new value stays in the file it was written to. The TUI feed reports `⚙ restart required: <keys>` (comma-separated key names; live-applied keys are reported separately as `⚙ settings applied: <keys>`), and the Settings screen annotates these rows with `(restart required)` (`SettingsRow.restart_required` in `novelizer/settings/view_model.py`). Restart Novelizer to pick the new values up — they are re-read from the normal layers on launch.

Live-applied examples, for contrast: cadence interval changes retune the affected agents in place, `max_concurrent_agents` updates the scheduler's dispatch limit (read fresh each tick), `muse_era` / `muse_exclusion_hands` update the Muse directly, and `voice_pack` / `prose_profile` are validated on apply — a pack that fails to load reverts both voice keys and reports an error instead of half-applying (an unknown profile name is not an error; it yields an empty casting note — see [Voice](#voice)). Temperature changes rebuild the affected agents' runners in place.

## Secret and hidden keys

Two key sets in `novelizer/settings/view_model.py` control what the Settings screen shows.

### Secret keys (`SECRET_KEYS`)

`SECRET_KEYS` contains two keys: `llm_api_key` and `embed_api_key`. Everywhere either value would be displayed, it is replaced with the redaction placeholder `••••••`:

- **Settings-screen row** — the value column shows `••••••` regardless of the actual value.
- **Edit confirmations** — the `✓ llm_api_key = •••••• (global) — watcher will apply it` message after an edit shows the placeholder, not what you typed.
- **Edit field** — selecting the row opens a password-masked input (the input's `password` flag is set for keys in `SECRET_KEYS`), and the field starts empty rather than pre-filled with the current key. (The setup wizard's API-key field is likewise password-masked.)

Redaction is display-only: the real values are still written in plain text to the global `config.toml`, which is why Novelizer always writes that file with mode `0600`. Both keys are also exactly the members of `FORBIDDEN_STORY_KEYS` — a hard `StoryConfigError` in `story.toml` (see key scoping above) — and both are restart-required.

### Hidden keys (`_HIDDEN_KEYS`)

Five keys exist on `EffectiveSettings` but never appear as Settings-screen rows:

| Key | Why it is hidden |
|---|---|
| `last_opened_story` | Managed by Novelizer: story picker preselection and headless story resolution |
| `suppress_flat_migration_prompt` | Set by Novelizer when you decline legacy flat-layout migration |
| `db_path` | Derived from the story directory, never configured |
| `chroma_path` | Derived from the story directory, never configured |
| `story_title` | Story metadata (the `title` key in `story.toml`), not a setting |

Hidden is not the same as unconfigurable: `last_opened_story` and `suppress_flat_migration_prompt` are ordinary global-only keys you can still set by editing `config.toml` (or via `NOVELIZER_LAST_OPENED_STORY` / `NOVELIZER_SUPPRESS_FLAT_MIGRATION_PROMPT`) — they are hidden only because Novelizer manages them itself. `db_path` and `chroma_path` are hidden because no layer accepts them at all, and `story_title` because it is set as `title` in `story.toml` (story metadata, editable there directly), not through the Settings screen.

## Field reference

Every key on `EffectiveSettings` (`novelizer/settings/models.py`), grouped by area. Defaults are the built-in field values from that model; for a copyable annotated global config, see [`docs/examples/config.example.toml`](../examples/config.example.toml).

The **Scope** column is the widest layer that accepts the key:

- `story` — story-overridable (`STORY_OVERRIDABLE_KEYS`): settable in `story.toml`, the global config, and the environment.
- `global` — global config and environment only; ignored with a warning in `story.toml` (`llm_api_key` is the exception: a hard error there).
- `story only` — accepted solely in `story.toml` (just `title`).
- `derived` — accepted by no layer; computed from the story directory.
- `fixed` — accepted by no layer, not even the environment; always the built-in default (the three `triage_*` keys).

Keys in `RESTART_REQUIRED_KEYS` are marked restart-required in their Notes; everything else applies live (see [restart section](#settings-that-require-a-restart-restart_required_keys)).

### Storage paths

| Key | Default | Scope | Notes |
|---|---|---|---|
| `db_path` | `stories/world.db` | derived | `<story-directory>/world.db` when a story is open |
| `chroma_path` | `stories/chroma` | derived | `<story-directory>/chroma` when a story is open |
| `embed_model` | `nomic-embed-text` | story | Embedding model name; restart-required |
| `embed_base_url` | `""` (reuse `llm_base_url`) | global | Dedicated OpenAI-compatible embedding endpoint; restart-required |
| `embed_api_key` | `""` | global | Secret (redacted in the Settings screen); forbidden in `story.toml`; restart-required |

**`db_path` and `chroma_path` are derived, never configured.** No layer accepts them — not `config.toml`, not `story.toml`, not the environment — and they are hidden from the Settings screen. When a story is open, `build_effective` (`novelizer/settings/loader.py`) overwrites both with paths computed from the story directory (`StoryDirectory` in `novelizer/settings/story_dir.py`): `db_path` becomes `<story-directory>/world.db` and `chroma_path` becomes `<story-directory>/chroma`. The built-in defaults above are only in effect when no story directory is given (e.g. settings loaded outside a story context).

`db_path` anchors every other storage file in the story: at startup the runtime derives sibling paths from it — `telemetry.db`, the `embeddings/` vector-index directory (a Chroma persistent store), `embed_cursor.json`, and `kg_cursor.json` all live next to `world.db` inside the story directory (`Runtime.start` in `novelizer/runtime.py`). This is what makes a story directory fully self-contained and movable.

`chroma_path` is a legacy key: nothing in the runtime reads the effective-settings value — the live vector index is the `embeddings/` directory above, not `chroma_path`. The `<story-directory>/chroma` location matters only to the flat-layout migration (`migrate_flat_layout` in `novelizer/settings/story_dir.py`), which moves a pre-story-directory `stories/chroma/` folder there when converting a legacy flat layout into a story directory.

**`embed_model`** is an ordinary configurable key: story-overridable (settable in `story.toml`, the global config, or `NOVELIZER_EMBED_MODEL`), and in `RESTART_REQUIRED_KEYS` like the other model keys — the embedding store is constructed once at startup. The model name is passed to an OpenAI embedding function (`EmbeddingStore` in `novelizer/store/embeddings.py`) along with the *resolved* embedding endpoint, so that endpoint must serve the named embedding model. Changing `embed_model` does not re-embed existing content; vectors already stored were produced by the previous model.

**`embed_base_url` and `embed_api_key`** decide which endpoint that is. Both are global-only (an installation fact, like `llm_base_url`) and default to empty, meaning *reuse the chat endpoint* — the single-endpoint local setup (Ollama, llama.cpp) needs neither key. Set `embed_base_url` when your chat endpoint serves no embedding models; hosted chat routers typically don't, and **OpenRouter serves none at all**, so an OpenRouter installation must point this at something else (a local Ollama, or OpenAI).

Resolution lives in two `EffectiveSettings` properties (`novelizer/settings/models.py`), which are what `Runtime.start` passes to `EmbeddingStore`:

- `resolved_embed_base_url` — `embed_base_url` when set (whitespace-only counts as unset), else `llm_base_url`.
- `resolved_embed_api_key` — `embed_api_key` when set. When `embed_base_url` is set but `embed_api_key` is not, this is `not-needed`, **not** `llm_api_key`: a separate embedding endpoint is a different provider, so the chat credential is deliberately never forwarded to it. Only the shared-endpoint case (no `embed_base_url`) falls back to `llm_api_key`.

### LLM endpoint and models

For pointing Novelizer at an endpoint as a task — llama.cpp, Ollama, LM Studio, or a hosted API — see [Connect a local LLM](../how-to/connect-a-local-llm.md). The first-run setup wizard writes these keys into the global config.

| Key | Default | Scope | Notes |
|---|---|---|---|
| `llm_base_url` | `http://localhost:8080/v1` | global | OpenAI-compatible endpoint; restart-required |
| `llm_api_key` | `not-needed` | global | Secret (redacted in the Settings screen); forbidden in `story.toml`; restart-required |
| `llm_max_tokens` | `4096` | global | Per-request generation cap for every runner; restart-required |
| `author_model` | `local-model` | story | Model for the Author (prose generation) and the Author chat persona; restart-required |
| `agent_model` | `local-model` | story | Model for every other LLM-backed agent; restart-required |
| `light_model` | `""` | story | Model for light-tier passes; empty falls back to `agent_model`; applies live |
| `light_reasoning` | `false` | story | Whether light-tier passes may open a thinking block; applies live |
| `author_temperature` | `0.8` | story | Author sampling temperature; applies live |
| `agent_temperature` | `0.7` | story | Sampling temperature for every other LLM-backed agent and all chat consultations; applies live |

**Endpoint.** Every model call goes through one OpenAI-compatible endpoint (`build_chat_model` in `novelizer/agents/llm.py` builds a chat model from `llm_base_url` + `llm_api_key`). Embeddings default to the same endpoint and key, but can be split onto their own endpoint with `embed_base_url` / `embed_api_key` (see [Storage paths](#storage-paths)) — required when the chat endpoint serves no embedding models, as with OpenRouter. Without that split, the server must serve both chat completions and the `embed_model` embedding model. Local servers that don't check credentials work with the default `llm_api_key` of `not-needed`.

**`llm_max_tokens`** is passed as the per-request `max_tokens` cap on every agent, chat, and research runner Novelizer builds (the Engine Room tool-call summarizer pins its own 40-token cap instead). It exists because an uncapped local model — especially one with server-side reasoning enabled — can generate past a proxy's request timeout, so no request ever completes; the caller sees a hang. Raise it if agents' JSON responses are being truncated mid-output; the Continuity Checker and KG extraction deliberately bound their inputs to keep dense responses under this cap.

**The light tier.** A third model tier, below `author_model` and `agent_model`, for work that is deterministic shaping of text someone else already wrote: the Flag Labeler's title and one-sentence summary, the `search_canon` context summary, and the Engine Room's tool-call summary. `light_model` defaults to empty, which means "no separate light model" — every light call runs on `agent_model`, so an installation that ignores this tier behaves exactly as before.

Which agents are light is declared per agent in the registry (`tier` on `AgentSpec`, `novelizer/agents/registry.py`), not decided inside each runner builder. The field is required, so a newly added agent must state its tier rather than inherit one. Only the Flag Labeler is light today: the Summarizer and KG extraction produce short output but reach it by reading prose and deciding what matters, which is judgment, so they stay full-tier.

Light passes also run graph-free. A deepagents graph exists so a model can choose tools and iterate; a light agent has no tools, so the graph around it was a state machine wrapped around a single request (`build_simple_runner` in `agent_kit/llm.py`).

**`light_reasoning`** controls whether light passes may think out loud. It defaults to `false`, sent as `enable_thinking` inside the request's `chat_template_kwargs` — the same key llama-server's `--reasoning on|off` sets server-wide, and the convention vLLM follows. Because it is a *chat-template* variable rather than a sampler parameter, it only takes effect if the loaded model's chat template actually branches on it (Qwen3-style templates do). Against a template that ignores it the flag is inert and harmless — treat suppression as a request, not a guarantee. Set it to `true` if you point `light_model` at a model whose short answers are better with a brief think.

Unlike the other model keys, `light_model` applies live: the Flag Labeler declares it in `rebuild_on` (so its runner is reconstructed in place), and the two summarizers build a model per call.

**Model split.** `author_model` is used only for prose generation and the Author chat persona; `agent_model` is used by everything else that calls the LLM: World Architect, Character Keeper, Editor, Continuity Checker (including its fact-mining pass), Retconner, Structure Analyst, Plotter, Triage, the non-Author chat personas, KG extraction, and the Engine Room tool-call summarizer — except where the light tier applies (see above). A dispatched researcher subagent inherits its dispatcher's model (`build_researcher_subagent` in `novelizer/agents/subagents.py` sets no model of its own), so the Author's researcher runs on `author_model` and every other agent's on `agent_model`. The Muse deals from card decks and makes no LLM calls. The split lets you put a stronger (or differently tuned) model on prose while the analytical fleet runs a cheaper one.

**Temperatures.** `author_temperature` applies to the Author's prose runner; `agent_temperature` applies to the rest of the fleet (researcher subagents inherit their dispatcher's runner, temperature included). A few internal runners pin their own temperature regardless of `agent_temperature`: KG extraction and the Continuity Checker's fact-mining pass at `0.2` (deterministic JSON over creative variance), and the tool-call summarizer at `0.0`. Chat consultations always use `agent_temperature`, even for the Author persona, though the Author persona still chats on `author_model` (`build_chat_runner` in `novelizer/chat/runners.py`).

**Restart vs. live.** `llm_base_url`, `llm_api_key`, `llm_max_tokens`, `author_model`, and `agent_model` are in `RESTART_REQUIRED_KEYS` — runners are constructed at startup, so changes wait for the next launch (see [the restart section](#settings-that-require-a-restart-restart_required_keys)). The two temperatures are not: `Runtime.apply_settings` rebuilds the affected agents' runners in place and clears the chat-runner cache when either changes.

Scope note: the endpoint keys are global-only — at most one chat endpoint and one embedding endpoint per installation — while the model and temperature keys are story-overridable, so a story can carry its own model choice in `story.toml`. `llm_api_key` and `embed_api_key` in a `story.toml` are a hard `StoryConfigError` (see [key scoping](#key-scoping-story-overridable-global-only-and-forbidden-keys)).

### Context and analysis thresholds

How much prose the context-assembling agents see, and how sensitive the Story Brain's pure detectors are. All four are story-overridable.

| Key | Default | Scope | Notes |
|---|---|---|---|
| `prior_chapter_summary_chars` | `200` | story | Chars of prose per prior chapter shown to the Author (of the last three); the most recent chapter is always included in full. Push mode only |
| `keeper_prose_chars` | `6000` | story | Chars of each of the last 5 chapters shown to the Character Keeper. Push mode only; must cover whole chapters or late-chapter characters are invisible to discovery |
| `staleness_threshold_chapters` | `3` | story | Chapters elapsed since a thread's last planted/touched event before it is flagged stale |
| `sag_spike_delta` | `0.3` | story | Tension deviation from the mean, in either direction, that flags a chapter as a sag or spike. Float |

**`prior_chapter_summary_chars`** controls the Author's view of earlier chapters (`_summarize` in `novelizer/agents/author.py`). The Author's context window covers the last three chapters (`Author.poll` passes `chapters[-3:]`): the ones before the most recent are listed as head slices — `prose[:prior_chapter_summary_chars]` each — while the most recent chapter is always included in full regardless of this setting: a uniform head slice hid the ending the next chapter has to pick up from, leaving the Author continuing from an opening. The cap applies only in push mode (when `author_tools_enabled` is off); a tooled Author gets a chapter index instead and pulls prose itself, so this key has no effect then.

**`keeper_prose_chars`** controls the Character Keeper's view of recent prose (`novelizer/agents/character_keeper.py`): each of the last five chapters appears as `prose[:keeper_prose_chars]`. It is explicitly a push-mode fallback — when `character_keeper_tools_enabled` is on, the Keeper gets a chapter index and reads full chapters via tools, because any prose cap has a cliff: a character introduced in a chapter's final scene is as canonical as one in the opening line, and a slice that cuts before their introduction makes them invisible to discovery. The default of `6000` is sized to cover whole chapters (the historical default of a few hundred chars was the root cause of missed character discovery); if your chapters run longer than 6000 chars, raise it or enable Keeper tools.

**`staleness_threshold_chapters`** feeds the pure staleness detector (`is_thread_stale` in `novelizer/brain/staleness.py`): a thread is stale once this many chapters have elapsed since the chapter of its last planted/touched event, with no terminal event since. Threads in a terminal state (`paid_off`/`abandoned`) are never stale; a thread whose last event carries no findable chapter reference counts every chapter as elapsed (conservatively, maximally stale). Two consumers share the same function so they can never disagree: the Author's prompt gains a "Stale threads (consider touching one, citing its id exactly)" block (`stale_threads_note` in `novelizer/brain/context.py`), and the TUI's Story Brain panel and story browser mark stale threads.

**`sag_spike_delta`** feeds the pure sag/spike detector (`detect_sag_spike` in `novelizer/brain/sag_spike.py`), which runs over the tension scores the Structure Analyst has already emitted — no LLM call. A chapter whose tension deviates from the mean of all scores by at least `delta` is flagged `sag` (below the mean) or `spike` (above it); with fewer than two scores nothing is flagged, since there is no mean worth deviating from. Lower values flag more chapters. Consumers: the Editor's prompt gains a "Pacing flags" block (`pacing_flags_note` in `novelizer/brain/context.py`), and the Story Brain panel shows the same flags.

**Live-apply nuance.** None of these keys is restart-required, and `Runtime.apply_settings` reports changes to them as applied. The TUI consumers genuinely pick a change up within a second — the brain and browser loops read `runtime.settings` fresh on every refresh cycle. The agent-side copies, however, are constructor arguments (`Author`, `CharacterKeeper`, and `Editor` capture their values when the runtime starts), and `apply_settings` does not push new values into running agents — so agent prompts reflect a changed threshold only after the next launch, even though the Story Brain display updates immediately.

### Outline gate

| Key | Default | Scope | Notes |
|---|---|---|---|
| `outline_gate_enabled` | `true` | global | Soft-gates `Author.readiness()` to `0.0` until a first-pass blueprint exists |

**`outline_gate_enabled`** implements outline-first genesis: with the gate on (the default), the Author reports zero readiness — so the scheduler never selects it — until the Plotter's blueprint has been adopted, or the [genesis fallback](../explanation/how-the-room-works.md) opens for an unattended run. It is global-only (settable in the global config or `NOVELIZER_OUTLINE_GATE_ENABLED`, ignored with a warning in `story.toml`) and applies live — `Runtime.apply_settings` picks it up on the next reload, no restart needed. Set it to `false` to restore the legacy outline-optional behavior: the Author drafts immediately and structure is retrofitted later. The gate itself lives in the readiness layer (`novelizer/brain/gate.py`, `author_may_draft`), not in the scheduler — it is soft, not a hard block, and only affects whether the Author is picked, never whether it is allowed to run if picked some other way.

### Muse

The Muse is the one agent with no LLM: it deals seeded hands of corpus draws — 5 names, 3 professions, 2 settings, 2 beats per hand (`novelizer/muse/draws.py`) — as `inspiration.*` events, keeping exactly one unconsumed hand ahead of the Author. These two keys shape its deals; its cadence is `muse_interval` (see [scheduler and agent cadence](#scheduler-and-agent-cadence-seconds)).

| Key | Default | Scope | Notes |
|---|---|---|---|
| `muse_era` | `modern` | story | Era bucket for given-name draws: `victorian`, `interwar`, `midcentury`, `late20th`, `modern`. An unknown value falls back to `modern` |
| `muse_exclusion_hands` | `3` | story | How many recent hands' items are excluded from a fresh deal. `0` (or any non-positive value) disables the exclusion window |

**`muse_era`** selects which era table in the bundled given-names corpus (`novelizer/muse/data/given_names.toml`) names are drawn from; surnames, professions, settings, and beats are not era-bucketed. A value that names no bucket in the corpus falls back to `modern` (`DEFAULT_ERA` in `novelizer/muse/draws.py`) rather than erroring, and each dealt hand records the bucket actually used in its `era` field.

**`muse_exclusion_hands`** keeps deals fresh: items dealt in the last *n* hands are excluded from the next deal (`_exclusion_window` in `novelizer/agents/muse.py`). For names, both components are excluded individually — dealing "Ada Voss" bars both "Ada" and "Voss" from the window, not just the exact pair. If the window would exhaust a corpus pool, the deal reuses the full pool instead of coming up short (degraded, never blocked). Deals are otherwise pure: identical `(corpora, seed, era, exclude)` always produce the identical hand, which event-log replay relies on.

Both keys are story-overridable — settable in `story.toml`, the global config, or `NOVELIZER_MUSE_ERA` / `NOVELIZER_MUSE_EXCLUSION_HANDS` — and both apply live: `Runtime.apply_settings` updates the running Muse directly, so the next deal uses the new values without a restart. The next deal happens when the Author consumes the active hand at chapter commit, or immediately on the director command `:muse reroll`, which supersedes the active hand and deals a fresh one.

### Scheduler and agent cadence (seconds)

How often each agent becomes eligible to run, and how many may run at once. Why these knobs exist — the scheduler and the agent fleet — is covered in [How the Room works](../explanation/how-the-room-works.md). All values are seconds; every interval except `projector_interval` is an integer.

| Key | Default | Scope | Applies to |
|---|---|---|---|
| `max_concurrent_agents` | `2` | global | Scheduler dispatch-pool size |
| `author_interval` | `300` | story | Author |
| `default_agent_interval` | `120` | story | World Architect, Character Keeper, Editor, Retconner |
| `continuity_interval` | `900` | story | Continuity Checker |
| `structure_analyst_interval` | `180` | story | Structure Analyst |
| `plotter_interval` | `240` | story | Plotter |
| `muse_interval` | `60` | story | Muse |
| `triage_interval` | `120` | fixed | Triage — not accepted by any configuration layer |
| `projector_interval` | `0.5` | story | TUI worker-loop sleep (projector catch-up and scheduler tick); float |

**What an interval means.** An interval is a *minimum spacing*, not a guarantee of running: an agent is eligible once its interval has elapsed since its last run started (`ready_for_interval` in `novelizer/agents/base.py`). Each scheduler tick fills the free dispatch-pool slots from the eligible agents, sorted by their readiness score — an eligible agent whose readiness is `0.0` (nothing to do, or its work fingerprint is unchanged) is skipped, and an agent that explicitly reports "nothing to do" backs off for 3× its interval (`PASS_BACKOFF_MULTIPLIER`) before becoming eligible again. A crashing agent also consumes its interval rather than hot-looping. Director override signals jump an agent to the front of the queue regardless of readiness ordering (`Scheduler.tick` in `novelizer/scheduler.py`).

**`max_concurrent_agents`** caps how many agents may be in flight simultaneously; each tick dispatches only into `max_concurrent - in_flight` free slots. It is global-only (ignored with a warning in `story.toml`) because it sizes the process's concurrency, not a story's pacing. A change applies live with no rebuilding: `Runtime.apply_settings` assigns the new limit directly onto the scheduler, and every tick computes free slots from the current value.

**The seven agent intervals** map to agents as shown in the table (`interval_map` in `Runtime.apply_settings`, `novelizer/runtime.py`; each agent is constructed with its interval from settings — the four `default_agent_interval` agents share one knob). `triage_interval` exists on `EffectiveSettings` and is passed to the Triage agent at construction, but no configuration layer accepts it — not the global config, not `story.toml`, not the environment — so Triage always runs on the built-in `120`.

**`projector_interval`** is not an agent cadence: it is the sleep between iterations of the TUI's background worker loops (`novelizer/tui/app.py`) — the projector/indexer/KG catch-up loop and the scheduler-tick loop both sleep `projector_interval` between cycles, so it is also the effective scheduler tick cadence while the TUI runs. It is the one float in the group; raising it makes the whole room less responsive, not any one agent slower.

**Live apply.** Every key in this section applies live. Interval changes retune the affected agents in place (`agent.interval` is reassigned; the new spacing takes effect from each agent's next eligibility check), `max_concurrent_agents` takes effect on the next tick, and `projector_interval` on the next loop iteration — the TUI loops read it from the runtime's settings each cycle. All agent intervals except `triage_interval` are story-overridable, so a story can carry its own pacing in `story.toml` (e.g. `NOVELIZER_AUTHOR_INTERVAL=60` or `author_interval = 60`).

### Voice

Which prose voice the Author writes in and the Editor enforces, plus the per-agent personality notes layered on top. Both keys are story-overridable and apply live.

| Key | Default | Scope | Notes |
|---|---|---|---|
| `voice_pack` | shipped `novelizer/voices/default.toml` | story | Filesystem path to a voice-pack TOML. The default is the shipped pack's absolute path, resolved via `importlib.resources` (`_DEFAULT_VOICE_PACK` in `novelizer/settings/models.py`) |
| `prose_profile` | `plain` | story | Name of a prose profile in the active pack; the shipped pack defines `sparse`, `lush`, and `plain` |

**Voice-pack file shape** (`VoicePack` in `novelizer/voices/models.py`, loaded by `load_voice_pack` in `novelizer/voices/loader.py`): a top-level `name`, one `[prose_profiles.<key>]` table per profile — each with `name` and `casting_note` string keys — and an optional `[agent_personalities]` table mapping agent names to one-line personality notes. A casting note is deliberately natural-language prose a human wrote, not a parameter DSL; it is handed to the agents verbatim.

**What the keys do.** `prose_profile` selects one profile from the pack, and that profile's casting note is injected into two prompts: the Author's drafting and revision prompts (`Write in this prose voice: …`, `novelizer/agents/author.py`) and the Editor's review prompt (`Enforce this prose voice: …; note any drift in your feedback`, `novelizer/agents/editor.py`). The pack's `agent_personalities` apply independently of the chosen profile: every agent gets its personality note at construction, and the chat personas use the same table (`novelizer/runtime.py`). Each chapter the Author commits records the pack name and profile (alongside model and temperature) in its provenance.

**Unknown profile names are not an error.** A `prose_profile` that names no profile in the active pack simply yields no casting note — the Author and Editor prompts omit the voice line entirely (`Runtime.start` and `apply_settings` both fall back to an empty note when `VoicePack.profile(...)` returns nothing). Only the pack itself is validated.

**Live apply and validation.** Neither key is restart-required. When either changes while running, `Runtime.apply_settings` reloads the pack: on success the new casting note is pushed into the Author and Editor and the personality notes into every agent, taking effect from the next draft (the Settings screen notes this: "voice & temperature affect the next draft"). If the pack fails to load — missing file (`Voice pack not found at '<path>'.`) or malformed TOML — the feed reports `⚙ settings error: voice_pack: <error>` and the running system keeps its previous values for *both* voice keys, because the pack and profile travel together. The file keeps what you wrote, so fixing the pack (or the path) re-triggers the apply within a second. At startup, a missing or unloadable pack is a hard error.

**Creating and inspecting packs.** `novelizer voices [--pack PATH]` prints a pack's profiles, agent personalities, and the story's character voice cards; `novelizer voice-scaffold PROFILE_NAME DESCRIPTION [--pack stories/user_pack.toml]` writes a new profile into a user pack — no LLM call, the description becomes the casting note verbatim, and writing into the shipped default pack is refused — see the [novelizer CLI reference](novelizer-cli.md). The story picker's new-story form offers a pack and profile picker; discovery (`discover_voice_packs` in `novelizer/voices/discovery.py`) lists the shipped default plus any `*.toml` files directly under the stories root — the `voice-scaffold` convention, whose default output is `stories/user_pack.toml`.

### Story metadata and app-level

The story's display title and the three keys Novelizer manages for itself: where stories live, which one you opened last, and whether to keep asking about legacy migration. None of these appear as Settings-screen rows (`story_title` and the two managed keys are [hidden](#secret-and-hidden-keys); `default_stories_dir` is an ordinary global key edited by the setup wizard or by hand).

| Key | Default | Scope | Notes |
|---|---|---|---|
| `story_title` / `title` | `None` | story only | Written as `title` in `story.toml`; surfaces as `story_title` in effective settings. Hidden from the Settings screen |
| `default_stories_dir` | `stories` | global | Root directory scanned by the story picker and used for headless story resolution; `~` is expanded |
| `last_opened_story` | `None` | global | Managed by Novelizer: absolute path of the last story opened. Picker ordering and second step of story resolution. Hidden |
| `suppress_flat_migration_prompt` | `false` | global | Set to `true` by Novelizer when you decline legacy flat-layout migration. Hidden |

**`story_title` / `title`** is the one key that runs opposite to every other: it is accepted *only* in `story.toml`, as `title` — the global config and the environment cannot set it. `build_effective` (`novelizer/settings/loader.py`) excludes `title` from the layer merge and passes it straight through as `story_title` on `EffectiveSettings`. It is story metadata, not a setting: `create_story(...)` writes it when the story is created (the story picker's new-story form supplies the name; the flat-layout migration writes `title = "default"`), the picker lists each story by it (`list_stories` in `novelizer/settings/discovery.py` falls back to the directory name when `title` is missing or the `story.toml` is unreadable), and the export screen pre-fills the export title with it (falling back to the story directory's name when it is unset). Edit it by editing `story.toml` directly.

**`default_stories_dir`** is the stories root: the directory the story picker scans for story directories, the base for headless story resolution, the location checked for a legacy flat layout (`<root>/world.db`), and the parent of the auto-created `<root>/default` story. It is relative to the current working directory unless absolute, and `~` is expanded (`Path(...).expanduser()` in `novelizer/director/cli.py`). The setup wizard offers it as a field on first run (blank keeps the default). Global-only: a story cannot relocate the root it lives in.

**`last_opened_story`** is written by Novelizer, not by you: after every successful story open — picker choice or headless resolution — the CLI writes the story root's path into the global config (`update_global_config(last_opened_story=...)` in `novelizer/director/cli.py`; the headless path only writes when a global config already exists). It is read in two places: the story picker lists the last-opened story first, remainder most-recently-written first (`order_stories` in `novelizer/settings/discovery.py`), and headless resolution opens it as step two — but only if the path still passes `is_story_dir`; a stale or deleted path is skipped silently and resolution falls through to the next step. Setting `NOVELIZER_LAST_OPENED_STORY` overrides what Novelizer reads, but Novelizer keeps writing its own value to the file.

**`suppress_flat_migration_prompt`** exists so "no" means no: when Novelizer finds a legacy flat story (`world.db` directly in the stories root) it offers to migrate it into `<root>/default/`; declining writes `suppress_flat_migration_prompt = true` to the global config, and from then on nothing prompts again. With suppression active, headless resolution uses the flat layout as-is (legacy paths keep working); the interactive TUI instead skips straight to the story picker, which lists only proper story directories. Delete the key from `config.toml` to be asked again.

All four keys are consumed at startup and story-pick time, not by the running room — `Runtime` never reads them, so changing them mid-session has no effect until the next launch or picker visit. The full story resolution order (`--story` flag → `last_opened_story` → legacy flat-layout migration → `<default_stories_dir>/default`, created if missing) is documented in the [novelizer CLI reference](novelizer-cli.md).

### Per-agent tool enablement flags

Whether each agent's runner is built with canon pull tooling. All default to `true`; all are story-overridable (settable in `story.toml`, the global config, or the environment, e.g. `NOVELIZER_AUTHOR_TOOLS_ENABLED=false`) except `triage_tools_enabled`, which is fixed. See [Connect a local LLM](../how-to/connect-a-local-llm.md) for when to disable tools for a given endpoint, and [How the Room works](../explanation/how-the-room-works.md) for what each agent does with them.

| Key | Default | Scope | Gates |
|---|---|---|---|
| `author_tools_enabled` | `true` | story | Author — also flips its context to pull mode |
| `checker_tools_enabled` | `true` | story | Continuity Checker — also flips its context to pull mode (the fact-mining pass is always tool-free) |
| `chat_tools_enabled` | `true` | story | All chat personas — also flips chat context to pull mode |
| `world_architect_tools_enabled` | `true` | story | World Architect toolkit only |
| `character_keeper_tools_enabled` | `true` | story | Character Keeper — also flips its context to pull mode |
| `editor_tools_enabled` | `true` | story | Editor toolkit only |
| `retconner_tools_enabled` | `true` | story | Retconner toolkit only |
| `structure_analyst_tools_enabled` | `true` | story | Structure Analyst — also flips its context to pull mode |
| `plotter_tools_enabled` | `true` | story | Plotter toolkit only |
| `triage_tools_enabled` | `true` | fixed | Triage — accepted by no configuration layer; always tooled |

**What "tools" means.** With a flag on, the agent's runner is built with the shared canon toolkit (`Runtime._phase_a_toolkit` in `novelizer/runtime.py`): a read-only virtual filesystem over the story canon (`CanonBackend` in `novelizer/canon_fs/backend.py`, with `/outline/`, `/skills/`, and a per-run scratch `/workspace/` routed alongside), plus the `search_canon` semantic-search tool (`novelizer/canon_fs/search.py`), which searches chapters, characters, world entries, threads, secrets, themes, promises, briefs, arcs, and knowledge-graph entities by meaning and returns canon file paths to read (capped at 20 hits). With the flag off, the runner is built bare — no backend, no tools (`Runtime._tooled` returns the plain builder unchanged).

**Pull mode vs push mode.** Five of the flags also change how the agent's prompt context is assembled, not just what tools exist:

- `author_tools_enabled` — a tooled Author gets a chapter index and pulls prose itself; untooled, it gets pushed head slices, and `prior_chapter_summary_chars` applies (see [context thresholds](#context-and-analysis-thresholds)).
- `character_keeper_tools_enabled` — a tooled Keeper reads full chapters via tools; untooled, `keeper_prose_chars` caps what it sees.
- `checker_tools_enabled` — same index-vs-pushed-prose switch for the Continuity Checker (`pull_mode` in `novelizer/agents/continuity_checker.py`).
- `structure_analyst_tools_enabled` — a tooled Analyst is given chapter ids and titles and reads the chapters itself; untooled, it scores 400-char excerpts — tension is a property of the whole arc, so the excerpt is the wrong unit (`novelizer/agents/structure_analyst.py`).
- `chat_tools_enabled` — tooled chat personas get a chapter index plus the canon toolkit; untooled, they get 200-char excerpts of the last three chapters pushed into the prompt (`ChatService._story_context` in `novelizer/chat/service.py`).

The other four configurable flags (`world_architect`, `editor`, `retconner`, `plotter`) gate only the toolkit; those agents' prompt assembly is the same either way.

**Interaction with subagent flags.** Turning a tools flag off also disables that agent's researcher subagent even if its [`*_subagent_enabled` flag](#per-agent-subagent-enablement-flags) is on — a subagent with no backend or tools to read from is moot, so `Runtime._tooled` only attaches subagents when `enabled` is true.

**Changes effectively require a restart.** These flags are not in `RESTART_REQUIRED_KEYS`, and the feed reports a change as `⚙ settings applied` — but tooling state is captured when the runtime starts (`Runtime._tooling_pinned`, the agents' `pull_mode` attributes, and `ChatService.pull_mode`), and `apply_settings` never rebuilds a runner for a tools-flag change; even the temperature-triggered runner rebuilds reuse the pinned startup values. In practice a changed flag takes effect on the next launch.

`triage_tools_enabled` exists on `EffectiveSettings` but appears in no configuration layer — not the global config, not `story.toml`, not the environment (`novelizer/settings/layers.py`, `novelizer/settings/loader.py`) — so Triage always runs with the toolkit.

### Per-agent subagent enablement flags

Whether each agent may delegate canon-reading to a **researcher subagent**. Separate from the [tools flags](#per-agent-tool-enablement-flags) and only meaningful when the matching `*_tools_enabled` flag is also on. All default to `false`; all are story-overridable (settable in `story.toml`, the global config, or the environment, e.g. `NOVELIZER_AUTHOR_SUBAGENT_ENABLED=true`) except `triage_subagent_enabled`, which is fixed. For why the fleet is structured this way, see [How the Room works](../explanation/how-the-room-works.md).

| Key | Default | Scope | Grants a researcher to |
|---|---|---|---|
| `world_architect_subagent_enabled` | `false` | story | World Architect |
| `character_keeper_subagent_enabled` | `false` | story | Character Keeper |
| `editor_subagent_enabled` | `false` | story | Editor |
| `retconner_subagent_enabled` | `false` | story | Retconner |
| `structure_analyst_subagent_enabled` | `false` | story | Structure Analyst |
| `plotter_subagent_enabled` | `false` | story | Plotter |
| `author_subagent_enabled` | `false` | story | Author |
| `checker_subagent_enabled` | `false` | story | Continuity Checker |
| `triage_subagent_enabled` | `false` | fixed | Triage — accepted by no configuration layer; never gets one |

**What the researcher is.** With a flag on, the agent's runner is built with one dispatchable subagent named `researcher` (`build_researcher_subagent` in `novelizer/agents/subagents.py`, attached via the `subagents=[...]` kwarg to the deepagents runner builder, which exposes it to the agent as a `task` dispatch tool). The researcher is a delegated canon-read worker: it is prompted to answer one precise question — "does chapter 12 show Mateo mentioning his debt?" — by reading, grepping, or `search_canon`-searching the story canon, then return a concise answer citing the file paths and record ids it consulted, rather than the dispatching agent burning its own context reading canon itself. Its spec sets no `tools` and no `model` key, so it inherits its dispatcher's canon toolkit and model automatically — the Author's researcher runs on `author_model`, every other agent's on `agent_model`. The same spec (same name, same description) is used for every dispatching agent; only the `{agent_name}` in its system prompt differs.

**Requires the matching tools flag.** A subagent flag does nothing unless the agent's `*_tools_enabled` flag is also on: `Runtime._tooled` (`novelizer/runtime.py`) attaches the researcher only on the tooled branch, because a researcher with no backend or tools to read from is moot. Off-by-default is deliberate — a dispatch is an extra nested LLM run on every use, and small local models often do better reading canon directly.

**Nine flags, not ten.** Chat has a tools flag (`chat_tools_enabled`) but no subagent flag — chat runners are never built with subagents. And `triage_subagent_enabled` exists on `EffectiveSettings` but appears in no configuration layer — not the global config, not `story.toml`, not the environment (`novelizer/settings/layers.py`, `novelizer/settings/loader.py`) — so Triage, though always tooled, never dispatches a researcher.

**Changes effectively require a restart.** Like the tools flags, these are not in `RESTART_REQUIRED_KEYS` — a change is reported as `⚙ settings applied` — but each agent reads its flag once, at construction (each agent's `_construct` passes it to `ctx.tooled(...)`), and `apply_settings` never rebuilds a runner for a subagent-flag change. In practice a changed flag takes effect on the next launch. One additional wrinkle: the live rebuild triggered by a temperature change reconstructs runners *without* the subagent argument, so a live `author_temperature`/`agent_temperature` edit drops an attached researcher from the rebuilt runners until the next launch.

## Unknown-key handling and validation errors

How each layer treats keys it does not recognize, and every error a configuration file can produce. The parsers live in `novelizer/settings/layers.py` (`parse_global`, `parse_story`) and `novelizer/settings/toml_io.py` (`load_toml_file`); the two exception types are `StoryConfigError` (`novelizer/settings/layers.py`) and `TOMLFileError` (`novelizer/settings/toml_io.py`).

### Unknown keys are ignored with a warning

Unknown keys in either TOML file never block loading — they are dropped from the parse and logged at `WARNING` level, one line per key, sorted by key name:

| Where | Log message |
|---|---|
| Global `config.toml` | `<path>: unknown setting '<key>' ignored` |
| `story.toml` | `<path>: unknown or global-only setting '<key>' ignored` |

"Unknown" is judged against the layer's own field set, which is why the `story.toml` wording says "unknown or global-only": a real key that is global-only (`llm_base_url`, `llm_max_tokens`, `embed_base_url`, `default_stories_dir`, `last_opened_story`, `suppress_flat_migration_prompt`, `max_concurrent_agents`) gets the same ignored-with-warning treatment in `story.toml` as a typo does. The rest of the file still loads — known keys keep their values.

Warnings go to the `novelizer.settings` logger, which — like all Novelizer logging — is routed to the rotating log file at `<config dir>/logs/novelizer.log` (`novelizer/logging_setup.py`), never to the terminal, so a misspelled key is easy to miss: check the log if a setting seems not to take.

Unrecognized `NOVELIZER_*` environment variables are the one silent case: `EnvOverrides` is declared with `extra="ignore"` (`novelizer/settings/loader.py`), so they are dropped without any warning.

### `StoryConfigError`: forbidden keys

`StoryConfigError` is raised in exactly two places, both about keys that must never be story-scoped:

- **A `FORBIDDEN_STORY_KEYS` key in `story.toml`** — `llm_api_key` or `embed_api_key`. `parse_story` raises before any field is read: `<path>: ['llm_api_key'] must not appear in story.toml — stories are shareable; secrets belong in the global config (<global config path>)`. This is a hard error, not a warning, because story directories are meant to be shared and must never carry secrets; the message names the file the key belongs in.
- **A non-story-overridable key passed to `create_story(..., overrides=...)`** (`novelizer/settings/story_dir.py`) — overrides are validated against `STORY_OVERRIDABLE_KEYS` *before* `mkdir`, and the error (`<story.toml path>: ['<key>', ...] are not story-overridable settings`) is raised with nothing on disk, so a bad call leaves no half-created story directory.

At startup, the CLI catches `StoryConfigError` (along with `TOMLFileError`) and exits with the message as a clean error rather than a traceback (`novelizer/director/cli.py`). Mid-session is less forgiving: the settings watcher's reload path catches only `TOMLFileError`, so adding `llm_api_key` to a `story.toml` while the TUI is running escapes the watcher's error handling instead of being reported to the feed — remove the key and restart.

### Malformed TOML: `TOMLFileError`

A file that cannot be parsed raises `TOMLFileError` with the message `<path>: invalid TOML: <detail>` (the detail includes tomllib's `at line N, column M`); a file that vanishes between the existence check and the read reports `<path>: file not found`.

- **At startup**: caught by the CLI and reported as a clean error; Novelizer does not launch.
- **While the TUI is running**: the settings watcher (`_settings_watch_loop` in `novelizer/tui/app.py`) catches it, writes a worker-error line to the feed, and keeps the previous settings — nothing is applied until the file parses again, at which point the next 1-second poll picks it up.
- **In the story picker**: a story whose `story.toml` will not parse is still listed, falling back to its directory name as the title (`list_stories` in `novelizer/settings/discovery.py`).

### Wrong-typed values for known keys

TOML values and `NOVELIZER_*` strings are validated by the layer models (pydantic): coercible values are coerced (e.g. the TOML string `"4096"` for the integer `llm_max_tokens`), and uncoercible ones raise a pydantic `ValidationError`. This error is not in the CLI's caught set, so it surfaces as a raw traceback rather than a friendly message — the config files' typed TOML values make it hard to hit in practice except via a mistyped environment variable.

### Settings-screen edit validation

Edits made in the Settings screen are validated by `parse_value` (`novelizer/settings/view_model.py`) *before* anything is written to a file. A value that does not parse as the key's declared type is rejected with `<key>: '<value>' is not a valid <type>` (e.g. `author_interval: 'abc' is not a valid int`); the message replaces the status line and the edit field stays open for another attempt. Booleans accept exactly `true`/`false`/`1`/`0`, case-insensitive. Because rejection happens pre-write, the watcher never sees a bad value — validation errors here can never reach the running system.

## See also

- [`docs/examples/config.example.toml`](../examples/config.example.toml) — annotated global `config.toml` you can copy into place; it restates the layering and scoping rules inline as comments.
- [novelizer CLI reference](novelizer-cli.md) — the `--story` flag and full story resolution order, the first-run setup wizard that writes the global config, and the `voices` / `voice-scaffold` commands for inspecting and creating voice packs.
- [Connect a local LLM](../how-to/connect-a-local-llm.md) — pointing Novelizer at llama.cpp, Ollama, LM Studio, or a hosted endpoint as a task: `llm_base_url`, models, and when to turn per-agent tool flags off.
- [How the Room works](../explanation/how-the-room-works.md) — why the scheduler, agent fleet, and researcher subagents exist; background for the cadence intervals and enablement flags above.
- The Settings screen (command palette entry `settings` in the TUI) edits every non-hidden key above at runtime, showing each row's winning source and restart-required status. Voice packs referenced by `voice_pack` are plain TOML files — inspect them with `novelizer voices`, create profiles with `novelizer voice-scaffold` (see [Voice](#voice)).
