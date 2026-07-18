# Settings & Configuration — Phase 3 (Settings Screen, Live-Apply, Provenance) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An in-TUI settings screen (inspect + edit global/story settings), live-apply of safe settings via a file watcher, restart-required labeling for the rest, and provenance stamped onto generated chapters.

**Architecture:** A pure view-model (`novelizer/settings/view_model.py`) computes rows (key, effective value, source layer, scope, editability) and applies edits by writing TOML files — the screen is a thin Textual `Screen` over it. The single apply path is: any edit (screen or hand-edit) writes the file → `NovelizerApp`'s watcher worker polls mtimes → reloads effective settings → `Runtime.apply_settings(new)` classifies each change as live-applied (cadence, voice/profile, temperatures) or restart-required (endpoint, models) and mutates the running agents accordingly. Provenance is a dict assembled by `Runtime` and stamped by `Author` into every `chapter.created` payload.

**Tech Stack:** Python ≥3.13, Textual 5.3 (`Screen`/`push_screen`, `DataTable`), pydantic v2, pytest + pilot tests, uv.

**Spec:** `docs/superpowers/specs/2026-07-18-settings-config-design.md` §6 (settings screen, live-apply), §7 (provenance). One rendering deviation from §6, chosen deliberately: instead of two visually separate "Global"/"This story" sections, the screen shows one table with a **Scope** column, story-scoped rows sorted first — same information, simpler in a `DataTable`.

**Facts about the current code this plan relies on** (verified during planning):
- Agent intervals are plain mutable attributes read live each scheduler tick (`BaseAgent.interval`, `ready_for_interval`); mutating them live-applies cadence with no scheduler changes.
- Model/temperature are baked into runners at build time (`build_author_runner(settings)` etc., called via `Runtime._runner_for`); live temperature change requires rebuilding the affected runner.
- Voice: `Runtime.start()` loads the pack once; agents hold `_casting_note` / `personality` strings that are re-interpolated into prompts every generation — updating those strings live-applies voice.
- `Author.commit()` builds a `Chapter` model and commits it as the `chapter.created` payload; `Chapter` is pydantic with defaults, so adding an optional field is event-log backward compatible.
- App worker loops read `self.runtime.settings.projector_interval` live each iteration, so replacing `runtime.settings` live-applies that too.

## Global Constraints

- Python ≥3.13; run everything via `uv run`.
- **Single apply path:** the screen never mutates the runtime directly — it writes TOML files; the watcher picks changes up. Hand-edits in an external editor flow through the identical path.
- Restart-required keys, exactly: `llm_base_url`, `llm_api_key`, `author_model`, `agent_model`, `embed_model`. Everything else editable is live-applied.
- `llm_api_key` is displayed redacted (`••••••`) and only ever written to the global config (scope mapping enforces this — story scope is structurally impossible for it).
- Rows whose value comes from a `NOVELIZER_*` env var are shown as source `env` and are not editable.
- App-managed keys (`last_opened_story`, `suppress_flat_migration_prompt`) and derived keys (`db_path`, `chroma_path`, `story_title`) do not appear on the screen.
- Provenance keys stamped on `chapter.created`: `model`, `temperature`, `voice_pack` (pack name), `prose_profile`.
- TDD red/green for every task; commit after every green.
- Known pre-existing flake, not yours: `tests/canon/test_event_store.py::test_sequences_are_strictly_increasing` (Hypothesis DeadlineExceeded).

---

### Task 1: Provenance on `chapter.created`

**Files:**
- Modify: `novelizer/store/models.py` (Chapter)
- Modify: `novelizer/agents/author.py` (constructor + commit)
- Modify: `novelizer/runtime.py` (assemble + pass provenance)
- Test: `tests/agents/test_author_provenance.py`

**Interfaces:**
- Consumes: existing `Author`, `Chapter`, `Runtime.start()`.
- Produces: `Chapter.provenance: dict | None = None`; `Author(..., provenance: dict | None = None)` storing `self.provenance`; every committed chapter carries it. `Runtime.start()` builds `{"model": s.author_model, "temperature": s.author_temperature, "voice_pack": self.voice_pack.name, "prose_profile": s.prose_profile}` and passes it to `Author`. (Spec §7 says "voice pack + version"; packs have no version field — the pack `name` is the identity we stamp. Note this in the commit message body if you like, not a code comment.)

- [ ] **Step 1: Write the failing test**

`tests/agents/test_author_provenance.py`:

```python
from novelizer.agents.author import Author
from novelizer.agents.base import ChapterDraft
from novelizer.canon.events import EventType


class _SpyCommitter:
    def __init__(self):
        self.commits = []

    async def commit(self, agent_name, event_type, aggregate_id, payload):
        self.commits.append((agent_name, event_type, payload))
        return None


class _NullRunner:
    async def ainvoke(self, inputs):
        raise AssertionError("not used")


async def test_commit_stamps_provenance():
    committer = _SpyCommitter()
    provenance = {
        "model": "m-big",
        "temperature": 0.8,
        "voice_pack": "default",
        "prose_profile": "plain",
    }
    author = Author(_NullRunner(), read_store=None, committer=committer, provenance=provenance)
    await author.commit(ChapterDraft(title="T", prose="P"))
    chapter_commits = [c for c in committer.commits if c[1] == EventType.CHAPTER_CREATED]
    assert len(chapter_commits) == 1
    assert chapter_commits[0][2].provenance == provenance


async def test_commit_without_provenance_is_none():
    committer = _SpyCommitter()
    author = Author(_NullRunner(), read_store=None, committer=committer)
    await author.commit(ChapterDraft(title="T", prose="P"))
    chapter = [c for c in committer.commits if c[1] == EventType.CHAPTER_CREATED][0][2]
    assert chapter.provenance is None
```

Adaptation note: read `novelizer/agents/author.py` first — `commit()` may also commit thread/knowledge/causal follow-up events and call `self._remark(...)`; if those paths dereference `self._read` or the draft's optional fields, extend `ChapterDraft(...)` in the test with whatever minimal fields keep `commit()` on the happy path, or stub `_SpyCommitter.commit` to return an object with the attributes the follow-up code expects. The two assertions above are the contract; adapt the scaffolding minimally and report what you changed.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/agents/test_author_provenance.py -v`
Expected: FAIL (`Chapter` has no `provenance`; `Author.__init__` rejects the kwarg).

- [ ] **Step 3: Implement**

In `novelizer/store/models.py`, add to `Chapter` after `editor_notes`:

```python
    provenance: Optional[dict] = None
```

In `novelizer/agents/author.py`:
- Add `provenance: dict | None = None` to `Author.__init__` (after `personality`), store `self.provenance = provenance`.
- In `commit()`, pass it into the `Chapter(...)` construction: `Chapter(title=draft.title, prose=draft.prose, character_ids=draft.character_ids, provenance=self.provenance)`.

In `novelizer/runtime.py` (`start()`), before constructing `Author`, build:

```python
        provenance = {
            "model": s.author_model,
            "temperature": s.author_temperature,
            "voice_pack": self.voice_pack.name,
            "prose_profile": s.prose_profile,
        }
```

and add `provenance=provenance` to the `Author(...)` call.

- [ ] **Step 4: Run tests to verify green + no regressions**

Run: `uv run pytest tests/agents tests/canon tests/tui -q`
Expected: all pass (Chapter's optional field is backward compatible; older serialized events lack the key and default to None).

- [ ] **Step 5: Commit**

```bash
git add novelizer/store/models.py novelizer/agents/author.py novelizer/runtime.py tests/agents/test_author_provenance.py
git commit -m "feat(canon): stamp generation provenance (model/temperature/voice) on chapter.created"
```

---

### Task 2: Settings view-model — rows, parsing, edits

**Files:**
- Create: `novelizer/settings/view_model.py`
- Test: `tests/settings/test_view_model.py`
- Modify: `novelizer/settings/__init__.py` (export `SettingsRow`, `build_settings_rows`, `load_layer_configs`, `apply_edit`, `RESTART_REQUIRED_KEYS`)

**Interfaces:**
- Consumes: `GlobalConfig`, `StoryConfig`, `EnvOverrides`, `EffectiveSettings`, `STORY_OVERRIDABLE_KEYS`, `parse_global`, `parse_story`, `load_toml_file`, `write_toml_file`, `update_global_config`, `global_config_path`, `StoryDirectory`.
- Produces:
  - `RESTART_REQUIRED_KEYS: frozenset[str]` = `{"llm_base_url", "llm_api_key", "author_model", "agent_model", "embed_model"}`.
  - `SettingsRow` — frozen dataclass: `key: str`, `value: str` (already redacted for secrets), `source: str` (`"default"|"global"|"story"|"env"`), `scope: str` (`"story"|"global"` — which file an edit writes), `editable: bool`, `restart_required: bool`.
  - `load_layer_configs(story_dir: StoryDirectory | None, global_path: Path | None = None) -> tuple[GlobalConfig, StoryConfig, EnvOverrides]` — file-reading helper (missing files → empty layers), mirrors the loader's parsing.
  - `build_settings_rows(global_cfg, story_cfg, env, effective) -> list[SettingsRow]` — pure; story-scoped rows first, alphabetical within scope; excludes app-managed/derived keys.
  - `parse_value(key: str, raw: str)` — pure; converts by the `EffectiveSettings` field's type (int/float/bool/str); `ValueError` with a readable message on bad input (bools accept true/false/1/0, case-insensitive).
  - `apply_edit(key: str, raw: str, story_dir: StoryDirectory, global_path: Path | None = None) -> str` — story-scoped key: set/update in `story.toml` (empty `raw` clears the override); global-scoped: `update_global_config`. Returns a one-line human message. Never mutates the runtime.

- [ ] **Step 1: Write the failing test**

`tests/settings/test_view_model.py`:

```python
from pathlib import Path

import pytest

from novelizer.settings import EffectiveSettings
from novelizer.settings.layers import GlobalConfig, StoryConfig
from novelizer.settings.loader import EnvOverrides
from novelizer.settings.story_dir import create_story
from novelizer.settings.toml_io import load_toml_file, write_toml_file
from novelizer.settings.view_model import (
    RESTART_REQUIRED_KEYS,
    SettingsRow,
    apply_edit,
    build_settings_rows,
    load_layer_configs,
    parse_value,
)


def _env(**kwargs) -> EnvOverrides:
    return EnvOverrides(_env_file=None, **kwargs)


def _rows(**layer_kwargs) -> dict[str, SettingsRow]:
    g = layer_kwargs.get("g", GlobalConfig())
    s = layer_kwargs.get("s", StoryConfig())
    e = layer_kwargs.get("e", _env())
    eff = layer_kwargs.get("eff", EffectiveSettings())
    return {r.key: r for r in build_settings_rows(g, s, e, eff)}


def test_source_resolution_and_scope():
    rows = _rows(
        g=GlobalConfig(author_model="gm", author_temperature=0.3),
        s=StoryConfig(author_temperature=0.9),
        e=_env(prose_profile="lush"),
        eff=EffectiveSettings(author_model="gm", author_temperature=0.9, prose_profile="lush"),
    )
    assert rows["author_model"].source == "global"
    assert rows["author_temperature"].source == "story"
    assert rows["prose_profile"].source == "env"
    assert rows["agent_model"].source == "default"
    assert rows["author_temperature"].scope == "story"
    assert rows["llm_base_url"].scope == "global"


def test_env_rows_not_editable_others_are():
    rows = _rows(e=_env(prose_profile="lush"), eff=EffectiveSettings(prose_profile="lush"))
    assert rows["prose_profile"].editable is False
    assert rows["author_temperature"].editable is True


def test_secret_redacted_and_restart_flags():
    rows = _rows(g=GlobalConfig(llm_api_key="sk-secret"), eff=EffectiveSettings(llm_api_key="sk-secret"))
    assert "sk-secret" not in rows["llm_api_key"].value
    assert rows["llm_api_key"].restart_required is True
    assert rows["author_temperature"].restart_required is False
    assert RESTART_REQUIRED_KEYS == {"llm_base_url", "llm_api_key", "author_model", "agent_model", "embed_model"}


def test_app_managed_and_derived_keys_hidden():
    rows = _rows()
    for hidden in ("last_opened_story", "suppress_flat_migration_prompt", "db_path", "chroma_path", "story_title"):
        assert hidden not in rows


def test_story_scope_rows_sort_first():
    ordered = build_settings_rows(GlobalConfig(), StoryConfig(), _env(), EffectiveSettings())
    scopes = [r.scope for r in ordered]
    assert scopes == sorted(scopes, key=lambda s: 0 if s == "story" else 1)


def test_parse_value_types():
    assert parse_value("author_interval", "120") == 120
    assert parse_value("author_temperature", "0.5") == 0.5
    assert parse_value("prose_profile", "lush") == "lush"
    with pytest.raises(ValueError):
        parse_value("author_interval", "not-a-number")


def test_apply_edit_story_scope_roundtrip(tmp_path):
    sd = create_story(tmp_path / "novel", title="N")
    apply_edit("author_temperature", "0.9", story_dir=sd, global_path=tmp_path / "g.toml")
    assert load_toml_file(sd.story_toml)["author_temperature"] == 0.9
    assert load_toml_file(sd.story_toml)["title"] == "N"  # preserved
    apply_edit("author_temperature", "", story_dir=sd, global_path=tmp_path / "g.toml")
    assert "author_temperature" not in load_toml_file(sd.story_toml)


def test_apply_edit_global_scope(tmp_path):
    sd = create_story(tmp_path / "novel", title="N")
    gpath = tmp_path / "g.toml"
    apply_edit("llm_base_url", "http://h:9/v1", story_dir=sd, global_path=gpath)
    assert load_toml_file(gpath)["llm_base_url"] == "http://h:9/v1"
    assert "llm_base_url" not in (load_toml_file(sd.story_toml))


def test_load_layer_configs(tmp_path):
    sd = create_story(tmp_path / "novel", title="N")
    write_toml_file(sd.story_toml, {"title": "N", "prose_profile": "lush"})
    gpath = tmp_path / "g.toml"
    write_toml_file(gpath, {"author_model": "gm"})
    g, s, e = load_layer_configs(sd, global_path=gpath)
    assert g.author_model == "gm"
    assert s.prose_profile == "lush"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/settings/test_view_model.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'novelizer.settings.view_model'`

- [ ] **Step 3: Implement**

`novelizer/settings/view_model.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from novelizer.settings.global_store import update_global_config
from novelizer.settings.layers import (
    GlobalConfig,
    StoryConfig,
    global_config_path,
    parse_global,
    parse_story,
)
from novelizer.settings.loader import EnvOverrides
from novelizer.settings.models import STORY_OVERRIDABLE_KEYS, EffectiveSettings
from novelizer.settings.story_dir import StoryDirectory
from novelizer.settings.toml_io import load_toml_file, write_toml_file

RESTART_REQUIRED_KEYS: frozenset[str] = frozenset({
    "llm_base_url", "llm_api_key", "author_model", "agent_model", "embed_model",
})

_SECRET_KEYS: frozenset[str] = frozenset({"llm_api_key"})
_HIDDEN_KEYS: frozenset[str] = frozenset({
    "last_opened_story", "suppress_flat_migration_prompt",
    "db_path", "chroma_path", "story_title",
})
_REDACTED = "••••••"


@dataclass(frozen=True)
class SettingsRow:
    key: str
    value: str
    source: str   # default | global | story | env
    scope: str    # story | global — which file an edit writes
    editable: bool
    restart_required: bool


def load_layer_configs(
    story_dir: StoryDirectory | None,
    global_path: Path | None = None,
) -> tuple[GlobalConfig, StoryConfig, EnvOverrides]:
    gpath = global_path if global_path is not None else global_config_path()
    global_cfg = parse_global(load_toml_file(gpath), source=str(gpath)) if gpath.exists() else GlobalConfig()
    story_cfg = StoryConfig()
    if story_dir is not None and story_dir.story_toml.exists():
        story_cfg = parse_story(load_toml_file(story_dir.story_toml), source=str(story_dir.story_toml))
    return global_cfg, story_cfg, EnvOverrides()


def build_settings_rows(
    global_cfg: GlobalConfig,
    story_cfg: StoryConfig,
    env: EnvOverrides,
    effective: EffectiveSettings,
) -> list[SettingsRow]:
    rows: list[SettingsRow] = []
    for key in EffectiveSettings.model_fields:
        if key in _HIDDEN_KEYS:
            continue
        if getattr(env, key, None) is not None:
            source = "env"
        elif getattr(story_cfg, key, None) is not None:
            source = "story"
        elif getattr(global_cfg, key, None) is not None:
            source = "global"
        else:
            source = "default"
        scope = "story" if key in STORY_OVERRIDABLE_KEYS else "global"
        value = _REDACTED if key in _SECRET_KEYS else str(getattr(effective, key))
        rows.append(SettingsRow(
            key=key,
            value=value,
            source=source,
            scope=scope,
            editable=source != "env",
            restart_required=key in RESTART_REQUIRED_KEYS,
        ))
    return sorted(rows, key=lambda r: (0 if r.scope == "story" else 1, r.key))


def parse_value(key: str, raw: str):
    annotation = EffectiveSettings.model_fields[key].annotation
    raw = raw.strip()
    try:
        if annotation is int:
            return int(raw)
        if annotation is float:
            return float(raw)
        if annotation is bool:
            if raw.lower() in ("true", "1"):
                return True
            if raw.lower() in ("false", "0"):
                return False
            raise ValueError(raw)
        return raw
    except ValueError:
        raise ValueError(f"{key}: {raw!r} is not a valid {getattr(annotation, '__name__', annotation)}") from None


def apply_edit(
    key: str,
    raw: str,
    story_dir: StoryDirectory,
    global_path: Path | None = None,
) -> str:
    """Write one edit to the owning file. The runtime is never touched here —
    the settings watcher picks the file change up (single apply path)."""
    scope = "story" if key in STORY_OVERRIDABLE_KEYS else "global"
    if scope == "global":
        value = parse_value(key, raw)
        update_global_config(path=global_path, **{key: value})
        return f"{key} = {value} (global)"
    data = load_toml_file(story_dir.story_toml) if story_dir.story_toml.exists() else {}
    if raw.strip() == "":
        data.pop(key, None)
        write_toml_file(story_dir.story_toml, data)
        return f"{key} cleared — inherits again"
    value = parse_value(key, raw)
    data[key] = value
    write_toml_file(story_dir.story_toml, data)
    return f"{key} = {value} (this story)"
```

Add to `novelizer/settings/__init__.py` imports and `__all__`: `RESTART_REQUIRED_KEYS`, `SettingsRow`, `apply_edit`, `build_settings_rows`, `load_layer_configs`, `parse_value` (from `novelizer.settings.view_model`).

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/settings/test_view_model.py -v`
Expected: 9 passed

- [ ] **Step 5: Commit**

```bash
git add novelizer/settings tests/settings/test_view_model.py
git commit -m "feat(settings): settings-screen view model — rows, sources, typed edits"
```

---

### Task 3: `Runtime.apply_settings` — live-apply with restart classification

**Files:**
- Modify: `novelizer/runtime.py`
- Test: `tests/test_apply_settings.py`

**Interfaces:**
- Consumes: `RESTART_REQUIRED_KEYS` (Task 2), `load_voice_pack`, existing agents/builders.
- Produces: `Runtime.apply_settings(new: EffectiveSettings) -> dict` with keys `"applied": list[str]` and `"restart_required": list[str]`. Behavior:
  - Cadence keys mutate the matching agents' `.interval` (`author_interval` → author; `default_agent_interval` → world_architect, character_keeper, editor, retconner; `continuity_interval` → continuity_checker; `structure_analyst_interval` → structure_analyst). `projector_interval` is applied implicitly (loops read `runtime.settings` live).
  - `voice_pack`/`prose_profile` changes reload the pack, update `author._casting_note`, `editor._casting_note`, every agent's `personality`, `self.voice_pack`/`self.active_prose_profile`, and the author's provenance dict.
  - `author_temperature`/`agent_temperature` changes rebuild the affected runners via the real `build_*_runner` functions — but only when the Runtime was constructed with real builders (`self._runners is None and self._runner is None`); with injected test runners, the change is recorded as applied without a rebuild. Provenance temperature updates either way.
  - Keys in `RESTART_REQUIRED_KEYS` are collected into `restart_required` and NOT applied (deliberately: the old value is what's actually still running, so provenance stays truthful).
  - Ends with `self.settings = new` — except restart-required fields, which are reverted onto the stored settings so `runtime.settings` always reflects reality. Implement by building the stored settings as `new.model_copy(update={k: getattr(self.settings, k) for k in restart_required_changed})`.
  - No changes → `{"applied": [], "restart_required": []}` and no side effects.

- [ ] **Step 1: Write the failing test**

`tests/test_apply_settings.py`:

```python
import os
import tempfile

from novelizer.runtime import Runtime
from novelizer.settings import EffectiveSettings


class _R:
    async def ainvoke(self, inputs):
        raise AssertionError("not used")


def _runners():
    names = [
        "author", "world_architect", "character_keeper", "editor",
        "continuity_checker", "retconner", "structure_analyst",
    ]
    return {n: _R() for n in names}


async def _started_runtime(**settings_kwargs) -> Runtime:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    rt = Runtime(EffectiveSettings(db_path=path, **settings_kwargs), runners=_runners())
    await rt.start()
    return rt


async def test_cadence_applies_live():
    rt = await _started_runtime(author_interval=300, default_agent_interval=120)
    result = rt.apply_settings(rt.settings.model_copy(update={"author_interval": 30, "default_agent_interval": 15}))
    assert rt.author.interval == 30
    assert rt.editor.interval == 15
    assert rt.retconner.interval == 15
    assert rt.continuity_checker.interval == rt.settings.continuity_interval
    assert set(result["applied"]) == {"author_interval", "default_agent_interval"}
    assert result["restart_required"] == []
    assert rt.settings.author_interval == 30
    await rt.close()


async def test_restart_required_not_applied():
    rt = await _started_runtime()
    old_url = rt.settings.llm_base_url
    result = rt.apply_settings(rt.settings.model_copy(update={"llm_base_url": "http://new:1/v1"}))
    assert result["restart_required"] == ["llm_base_url"]
    assert rt.settings.llm_base_url == old_url  # runtime reflects what actually runs
    await rt.close()


async def test_temperature_updates_provenance_with_injected_runners():
    rt = await _started_runtime(author_temperature=0.8)
    result = rt.apply_settings(rt.settings.model_copy(update={"author_temperature": 0.2}))
    assert "author_temperature" in result["applied"]
    assert rt.author.provenance["temperature"] == 0.2
    assert rt.settings.author_temperature == 0.2
    await rt.close()


async def test_prose_profile_change_updates_casting_and_provenance():
    rt = await _started_runtime()
    old_note = rt.author._casting_note
    result = rt.apply_settings(rt.settings.model_copy(update={"prose_profile": "__nonexistent__"}))
    assert "prose_profile" in result["applied"]
    assert rt.author.provenance["prose_profile"] == "__nonexistent__"
    assert rt.author._casting_note != old_note  # unknown profile -> empty casting note
    await rt.close()


async def test_no_changes_is_noop():
    rt = await _started_runtime()
    assert rt.apply_settings(rt.settings) == {"applied": [], "restart_required": []}
    await rt.close()
```

Adaptation note: `test_prose_profile_change_updates_casting_and_provenance` assumes `VoicePack.profile("__nonexistent__")` returns `None` (mirroring `Runtime.start()`'s `if self.active_prose_profile else ""` guard). Read `novelizer/voices/` first: if an unknown profile raises instead, `apply_settings` must catch that and fall back to an empty casting note (matching start()'s tolerance), and the test stands. If the shipped default pack has a second real profile, you may additionally assert with it, but keep the unknown-profile fallback test — hand-edited story.toml files will contain typos.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_apply_settings.py -v`
Expected: FAIL with `AttributeError: 'Runtime' object has no attribute 'apply_settings'`

- [ ] **Step 3: Implement**

In `novelizer/runtime.py`, add imports:

```python
from novelizer.settings import EffectiveSettings, RESTART_REQUIRED_KEYS
```

(replace the existing `EffectiveSettings` import line), and add the method to `Runtime`:

```python
    def apply_settings(self, new: EffectiveSettings) -> dict:
        """Apply a freshly loaded EffectiveSettings to the running system.

        Cadence, voice, and temperatures apply live; endpoint/model changes are
        reported as restart-required and left un-applied so self.settings always
        reflects what is actually running.
        """
        old = self.settings
        changed = [k for k in EffectiveSettings.model_fields if getattr(old, k) != getattr(new, k)]
        applied: list[str] = []
        restart: list[str] = []
        interval_map = {
            "author_interval": [self.author],
            "default_agent_interval": [self.world_architect, self.character_keeper, self.editor, self.retconner],
            "continuity_interval": [self.continuity_checker],
            "structure_analyst_interval": [self.structure_analyst],
        }
        for key in changed:
            if key in RESTART_REQUIRED_KEYS:
                restart.append(key)
            elif key in interval_map:
                for agent in interval_map[key]:
                    agent.interval = getattr(new, key)
                applied.append(key)
            else:
                applied.append(key)

        if "voice_pack" in changed or "prose_profile" in changed:
            self.voice_pack = load_voice_pack(new.voice_pack)
            try:
                self.active_prose_profile = self.voice_pack.profile(new.prose_profile)
            except Exception:
                self.active_prose_profile = None
            casting_note = self.active_prose_profile.casting_note if self.active_prose_profile else ""
            personalities = self.voice_pack.agent_personalities
            self.author._casting_note = casting_note
            self.editor._casting_note = casting_note
            for agent in self.agents:
                agent.personality = personalities.get(agent.name, "")

        rebuild = self._runners is None and self._runner is None
        if "author_temperature" in changed and rebuild:
            self.author._runner = build_author_runner(new)
        if "agent_temperature" in changed and rebuild:
            self.world_architect._runner = build_world_architect_runner(new)
            self.character_keeper._runner = build_character_keeper_runner(new)
            self.editor._runner = build_editor_runner(new)
            self.continuity_checker._runner = build_continuity_checker_runner(new)
            self.retconner._runner = build_retconner_runner(new)
            self.structure_analyst._runner = build_structure_analyst_runner(new)

        if self.author is not None and self.author.provenance is not None:
            self.author.provenance = {
                "model": old.author_model,  # restart-required: old model still runs
                "temperature": new.author_temperature,
                "voice_pack": self.voice_pack.name,
                "prose_profile": new.prose_profile,
            }

        self.settings = new.model_copy(update={k: getattr(old, k) for k in restart}) if restart else new
        return {"applied": applied, "restart_required": restart}
```

Adaptation notes: (a) verify the runner attribute name on `BaseAgent` (`novelizer/agents/base.py`) — the plan assumes `self._runner`; use whatever the real name is. (b) `apply_settings` is only valid after `start()` (agents exist) — that's the only call site (the watcher). (c) If `VoicePack.profile` already returns `None` for unknown names, drop the `try/except`.

- [ ] **Step 4: Run tests to verify green + no regressions**

Run: `uv run pytest tests/test_apply_settings.py tests/test_runtime.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add novelizer/runtime.py tests/test_apply_settings.py
git commit -m "feat(runtime): apply_settings — live cadence/voice/temperature, restart-required classification"
```

---

### Task 4: Settings file watcher in the main app

**Files:**
- Modify: `novelizer/tui/app.py` (new worker + mount registration + class attr)
- Test: `tests/tui/test_settings_watch.py`

**Interfaces:**
- Consumes: `Runtime.apply_settings` (Task 3), `load_effective_settings`, `StoryDirectory`, `global_config_path`, `TOMLFileError`.
- Produces: `NovelizerApp.SETTINGS_POLL_INTERVAL: float = 1.0` (class attr, overridable in tests) and `_settings_watch_loop()` worker registered in `on_mount`. Behavior: watches mtimes of `<story>/story.toml` (story dir = `Path(runtime.settings.db_path).parent`) and `global_config_path()`; on change, reloads effective settings and calls `runtime.apply_settings`; writes feed lines `⚙ settings applied: a, b` and/or `⚙ restart required: x` (only when non-empty); a `TOMLFileError` (mid-edit invalid file) is reported to the feed and the loop keeps running.

- [ ] **Step 1: Write the failing test**

`tests/tui/test_settings_watch.py`:

```python
from novelizer.runtime import Runtime
from novelizer.settings import EffectiveSettings, create_story
from novelizer.settings.toml_io import load_toml_file, write_toml_file
from novelizer.tui.app import NovelizerApp
from tests.tui.test_app_smoke import _room_runners


async def _story_app(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    sd = create_story(tmp_path / "novel", title="N")
    settings = EffectiveSettings(
        db_path=str(sd.db_path),
        chroma_path=str(sd.chroma_path),
        author_interval=300,
        projector_interval=0.1,
    )
    rt = Runtime(settings, runners=_room_runners())
    await rt.start()
    app = NovelizerApp(rt)
    app.SETTINGS_POLL_INTERVAL = 0.05
    return app, rt, sd


async def test_story_toml_edit_applies_live(tmp_path, monkeypatch):
    app, rt, sd = await _story_app(tmp_path, monkeypatch)
    try:
        async with app.run_test() as pilot:
            await pilot.pause(0.2)
            data = load_toml_file(sd.story_toml)
            data["author_interval"] = 30
            write_toml_file(sd.story_toml, data)
            await pilot.pause(0.5)
            assert rt.author.interval == 30
            assert any("settings applied" in m for m in app.messages)
    finally:
        await rt.close()


async def test_invalid_story_toml_reports_not_crashes(tmp_path, monkeypatch):
    app, rt, sd = await _story_app(tmp_path, monkeypatch)
    try:
        async with app.run_test() as pilot:
            await pilot.pause(0.2)
            sd.story_toml.write_text("author_interval = \n")
            await pilot.pause(0.5)
            assert rt.author.interval == 300  # unchanged
            # loop survived: fix the file and it still applies
            write_toml_file(sd.story_toml, {"title": "N", "author_interval": 45})
            await pilot.pause(0.5)
            assert rt.author.interval == 45
    finally:
        await rt.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/tui/test_settings_watch.py -v`
Expected: FAIL — intervals never change (no watcher exists).

- [ ] **Step 3: Implement**

In `novelizer/tui/app.py`, add imports:

```python
from pathlib import Path
from novelizer.settings import StoryDirectory, TOMLFileError, global_config_path, load_effective_settings
```

Add class attr near `TITLE`:

```python
    SETTINGS_POLL_INTERVAL: float = 1.0
```

Register in `on_mount` alongside the other workers:

```python
        self.run_worker(self._settings_watch_loop(), exclusive=False)
```

Add the worker:

```python
    async def _settings_watch_loop(self) -> None:
        story_dir = StoryDirectory(root=Path(self.runtime.settings.db_path).parent)
        watched = [story_dir.story_toml, global_config_path()]

        def snapshot() -> tuple:
            return tuple(p.stat().st_mtime if p.exists() else 0.0 for p in watched)

        last = snapshot()
        while True:
            await asyncio.sleep(self.SETTINGS_POLL_INTERVAL)
            current = snapshot()
            if current == last:
                continue
            last = current
            try:
                new_settings = load_effective_settings(story_dir=story_dir)
            except TOMLFileError as e:
                self._report_worker_error("settings", e)
                continue
            try:
                result = self.runtime.apply_settings(new_settings)
            except Exception as e:
                self._report_worker_error("settings", e)
                continue
            log = self.query_one("#feed", RichLog)
            if result["applied"]:
                line = f"⚙ settings applied: {', '.join(result['applied'])}"
                log.write(line)
                self.messages.append(line)
            if result["restart_required"]:
                line = f"⚙ restart required: {', '.join(result['restart_required'])}"
                log.write(line)
                self.messages.append(line)
```

- [ ] **Step 4: Run tests to verify green + no TUI regressions**

Run: `uv run pytest tests/tui -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add novelizer/tui/app.py tests/tui/test_settings_watch.py
git commit -m "feat(tui): settings file watcher — hand-edits and screen edits live-apply via one path"
```

---

### Task 5: Settings screen — display and `:settings` command

**Files:**
- Create: `novelizer/tui/settings_screen.py`
- Modify: `novelizer/tui/app.py` (`_run_command` intercept)
- Modify: `novelizer/tui/app.tcss` (screen styles)
- Test: `tests/tui/test_settings_screen.py` (display parts)

**Interfaces:**
- Consumes: `build_settings_rows`, `load_layer_configs`, `SettingsRow` (Task 2), `StoryDirectory`.
- Produces: `SettingsScreen(Screen)` — constructor `SettingsScreen(story_dir: StoryDirectory, effective_getter, probe=probe_endpoint)` where `effective_getter()` returns the current `EffectiveSettings` (pass `lambda: app.runtime.settings`). Widgets: `#settings_table` (DataTable, columns: Setting | Value | Source | Scope | Notes), `#edit_value` (Input, hidden until editing), `#settings_msg` (Static). Bindings: `escape` → dismiss, `t` → test connection (Task 6), `enter` on a row → edit (Task 6). Notes column: `(inherited)` when scope=="story" and source in ("default","global"); `(env — read only)` when source=="env"; `(restart required)` when flagged. Typing `:settings` (or `settings`) in the command input pushes the screen; `commands.dispatch` is untouched.

- [ ] **Step 1: Write the failing test**

`tests/tui/test_settings_screen.py`:

```python
from novelizer.runtime import Runtime
from novelizer.settings import EffectiveSettings, create_story
from novelizer.tui.app import NovelizerApp
from novelizer.tui.settings_screen import SettingsScreen
from tests.tui.test_app_smoke import _room_runners
from textual.widgets import DataTable


async def _app(tmp_path, monkeypatch, **settings_kwargs):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    sd = create_story(tmp_path / "novel", title="N")
    settings = EffectiveSettings(
        db_path=str(sd.db_path), chroma_path=str(sd.chroma_path),
        projector_interval=0.1, **settings_kwargs,
    )
    rt = Runtime(settings, runners=_room_runners())
    await rt.start()
    return NovelizerApp(rt), rt, sd


async def test_settings_command_opens_screen_with_rows(tmp_path, monkeypatch):
    app, rt, sd = await _app(tmp_path, monkeypatch)
    try:
        async with app.run_test() as pilot:
            await app._run_command(":settings")
            await pilot.pause()
            assert isinstance(app.screen, SettingsScreen)
            table = app.screen.query_one("#settings_table", DataTable)
            assert table.row_count > 10
    finally:
        await rt.close()


async def test_api_key_redacted_and_inherited_marked(tmp_path, monkeypatch):
    app, rt, sd = await _app(tmp_path, monkeypatch, llm_api_key="sk-very-secret")
    try:
        async with app.run_test() as pilot:
            await app._run_command("settings")
            await pilot.pause()
            table = app.screen.query_one("#settings_table", DataTable)
            cells = [str(cell) for row_key in list(table.rows) for cell in table.get_row(row_key)]
            joined = " | ".join(cells)
            assert "sk-very-secret" not in joined
            assert "(inherited)" in joined
            assert "(restart required)" in joined
    finally:
        await rt.close()


async def test_escape_dismisses(tmp_path, monkeypatch):
    app, rt, sd = await _app(tmp_path, monkeypatch)
    try:
        async with app.run_test() as pilot:
            await app._run_command(":settings")
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()
            assert not isinstance(app.screen, SettingsScreen)
    finally:
        await rt.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/tui/test_settings_screen.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'novelizer.tui.settings_screen'`

- [ ] **Step 3: Implement**

`novelizer/tui/settings_screen.py`:

```python
from __future__ import annotations

from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Input, Static

from novelizer.settings import build_settings_rows, load_layer_configs
from novelizer.settings.setup_core import probe_endpoint
from novelizer.settings.story_dir import StoryDirectory


class SettingsScreen(Screen):
    """Read/edit settings. Edits write TOML files only; the app's settings
    watcher applies them to the runtime (single apply path)."""

    BINDINGS = [
        ("escape", "dismiss_screen", "Back"),
        ("t", "test_connection", "Test connection"),
    ]

    def __init__(self, story_dir: StoryDirectory, effective_getter, probe=probe_endpoint) -> None:
        super().__init__()
        self._story_dir = story_dir
        self._effective = effective_getter
        self._probe = probe
        self._rows = []

    def compose(self) -> ComposeResult:
        yield Static("Settings — edits write config files; safe changes apply live", id="settings_title")
        table = DataTable(id="settings_table")
        yield table
        yield Input(id="edit_value", placeholder="new value (empty clears a story override)")
        yield Static("", id="settings_msg")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#edit_value", Input).display = False
        table = self.query_one("#settings_table", DataTable)
        table.cursor_type = "row"
        table.add_columns("Setting", "Value", "Source", "Scope", "Notes")
        self.refresh_rows()
        table.focus()

    def refresh_rows(self) -> None:
        global_cfg, story_cfg, env = load_layer_configs(self._story_dir)
        self._rows = build_settings_rows(global_cfg, story_cfg, env, self._effective())
        table = self.query_one("#settings_table", DataTable)
        table.clear()
        for row in self._rows:
            notes = []
            if row.scope == "story" and row.source in ("default", "global"):
                notes.append("(inherited)")
            if row.source == "env":
                notes.append("(env — read only)")
            if row.restart_required:
                notes.append("(restart required)")
            table.add_row(row.key, row.value, row.source, row.scope, " ".join(notes))

    def action_dismiss_screen(self) -> None:
        self.app.pop_screen()

    def action_test_connection(self) -> None:
        pass  # Task 6
```

In `novelizer/tui/app.py`, at the top of `_run_command`, intercept before dispatch:

```python
    async def _run_command(self, line: str) -> None:
        cmd = line.strip().lstrip(":").split(maxsplit=1)
        if cmd and cmd[0].lower() == "settings":
            from novelizer.tui.settings_screen import SettingsScreen

            story_dir = StoryDirectory(root=Path(self.runtime.settings.db_path).parent)
            self.push_screen(SettingsScreen(story_dir, lambda: self.runtime.settings))
            return
        result = await commands.dispatch(self.runtime, line)
        ...
```

(keep the existing body after the intercept; `StoryDirectory`/`Path` imports exist from Task 4).

In `novelizer/tui/app.tcss`, append:

```css
#settings_table { height: 1fr; }
#settings_msg { height: 1; }
#edit_value { height: 3; }
```

Also update the `_status_line` hint string in app.py to mention `:settings` (append `· :settings` to the command list in the string) and update any test asserting that exact string if one exists (grep `tests/` for a failing assertion after the change and adjust it minimally).

- [ ] **Step 4: Run tests to verify green + no TUI regressions**

Run: `uv run pytest tests/tui -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add novelizer/tui/settings_screen.py novelizer/tui/app.py novelizer/tui/app.tcss tests/tui/test_settings_screen.py
git commit -m "feat(tui): settings screen — layered view with sources, scopes, and :settings command"
```

---

### Task 6: Settings screen — editing and test connection

**Files:**
- Modify: `novelizer/tui/settings_screen.py`
- Test: `tests/tui/test_settings_screen.py` (extend)

**Interfaces:**
- Consumes: `apply_edit`, `parse_value` (Task 2), `probe_endpoint` (injectable, already a constructor param).
- Produces: pressing enter on a row reveals `#edit_value` prefilled with the row's current value (empty for secrets); submitting writes via `apply_edit` and refreshes; empty submit on a story-scope row clears the override; env rows show "read only" in `#settings_msg` instead of editing; invalid values show the `ValueError` message; `t` runs the probe against the current effective endpoint and shows the result in `#settings_msg`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/tui/test_settings_screen.py`:

```python
from novelizer.settings.setup_core import ProbeResult
from novelizer.settings.toml_io import load_toml_file
from novelizer.tui.settings_screen import SettingsScreen
from textual.widgets import Input, Static


def _row_index(screen: SettingsScreen, key: str) -> int:
    return next(i for i, r in enumerate(screen._rows) if r.key == key)


async def test_edit_story_scope_writes_story_toml(tmp_path, monkeypatch):
    app, rt, sd = await _app(tmp_path, monkeypatch)
    try:
        async with app.run_test() as pilot:
            await app._run_command(":settings")
            await pilot.pause()
            screen = app.screen
            table = screen.query_one("#settings_table", DataTable)
            table.move_cursor(row=_row_index(screen, "author_temperature"))
            await pilot.press("enter")
            box = screen.query_one("#edit_value", Input)
            assert box.display
            box.value = "0.25"
            box.focus()
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            assert load_toml_file(sd.story_toml)["author_temperature"] == 0.25
    finally:
        await rt.close()


async def test_clear_story_override(tmp_path, monkeypatch):
    app, rt, sd = await _app(tmp_path, monkeypatch)
    from novelizer.settings.toml_io import write_toml_file

    write_toml_file(sd.story_toml, {"title": "N", "author_temperature": 0.9})
    try:
        async with app.run_test() as pilot:
            await app._run_command(":settings")
            await pilot.pause()
            screen = app.screen
            table = screen.query_one("#settings_table", DataTable)
            table.move_cursor(row=_row_index(screen, "author_temperature"))
            await pilot.press("enter")
            box = screen.query_one("#edit_value", Input)
            box.value = ""
            box.focus()
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            assert "author_temperature" not in load_toml_file(sd.story_toml)
    finally:
        await rt.close()


async def test_env_row_not_editable(tmp_path, monkeypatch):
    monkeypatch.setenv("NOVELIZER_PROSE_PROFILE", "lush")
    app, rt, sd = await _app(tmp_path, monkeypatch)
    try:
        async with app.run_test() as pilot:
            await app._run_command(":settings")
            await pilot.pause()
            screen = app.screen
            table = screen.query_one("#settings_table", DataTable)
            table.move_cursor(row=_row_index(screen, "prose_profile"))
            await pilot.press("enter")
            assert screen.query_one("#edit_value", Input).display is False
            assert "read only" in str(screen.query_one("#settings_msg", Static).renderable)
    finally:
        await rt.close()


async def test_invalid_value_shows_error(tmp_path, monkeypatch):
    app, rt, sd = await _app(tmp_path, monkeypatch)
    try:
        async with app.run_test() as pilot:
            await app._run_command(":settings")
            await pilot.pause()
            screen = app.screen
            table = screen.query_one("#settings_table", DataTable)
            table.move_cursor(row=_row_index(screen, "author_interval"))
            await pilot.press("enter")
            box = screen.query_one("#edit_value", Input)
            box.value = "soon"
            box.focus()
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            assert "not a valid" in str(screen.query_one("#settings_msg", Static).renderable)
            assert "author_interval" not in load_toml_file(sd.story_toml)
    finally:
        await rt.close()


async def test_probe_action_shows_result(tmp_path, monkeypatch):
    app, rt, sd = await _app(tmp_path, monkeypatch)

    async def fake_probe(base_url, api_key="not-needed", **kwargs):
        return ProbeResult(ok=True, models=["m-live"])

    try:
        async with app.run_test() as pilot:
            story_dir_screen = SettingsScreen(
                sd if hasattr(sd, "story_toml") else None,
                lambda: rt.settings,
                probe=fake_probe,
            )
            await app.push_screen(story_dir_screen)
            await pilot.pause()
            await pilot.press("t")
            await pilot.pause()
            assert "m-live" in str(story_dir_screen.query_one("#settings_msg", Static).renderable)
    finally:
        await rt.close()
```

(Note: `sd` from `_app` IS a `StoryDirectory` — the `hasattr` guard is belt-and-braces from the template; simplify to just `sd` when implementing.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/tui/test_settings_screen.py -v`
Expected: new tests FAIL (no editing behavior); Task 5 tests still pass.

- [ ] **Step 3: Implement**

In `novelizer/tui/settings_screen.py`, add imports `apply_edit` (from `novelizer.settings`) and extend the class:

```python
    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        self._begin_edit(event.cursor_row)

    def _begin_edit(self, row_index: int) -> None:
        if not (0 <= row_index < len(self._rows)):
            return
        row = self._rows[row_index]
        msg = self.query_one("#settings_msg", Static)
        if not row.editable:
            msg.update(f"{row.key} is set by NOVELIZER_{row.key.upper()} — read only here")
            return
        self._editing_key = row.key
        box = self.query_one("#edit_value", Input)
        box.display = True
        box.value = "" if row.value == "••••••" else row.value
        msg.update(f"editing {row.key} ({row.scope}) — empty clears a story override")
        box.focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "edit_value" or not getattr(self, "_editing_key", None):
            return
        msg = self.query_one("#settings_msg", Static)
        try:
            outcome = apply_edit(self._editing_key, event.value, story_dir=self._story_dir)
        except ValueError as e:
            msg.update(str(e))
            return
        self._editing_key = None
        event.input.display = False
        event.input.value = ""
        self.refresh_rows()
        msg.update(f"✓ {outcome} — watcher will apply it")
        self.query_one("#settings_table", DataTable).focus()

    def action_test_connection(self) -> None:
        self.run_worker(self._run_probe(), exclusive=True)

    async def _run_probe(self) -> None:
        effective = self._effective()
        result = await self._probe(effective.llm_base_url, api_key=effective.llm_api_key)
        msg = self.query_one("#settings_msg", Static)
        if result.ok:
            msg.update(f"✓ connected — models: {', '.join(result.models) or '(none reported)'}")
        else:
            msg.update(f"✗ {result.error}")
```

Replace Task 5's `action_test_connection` stub with the above. Note the `enter` keybinding: `DataTable` with `cursor_type="row"` emits `RowSelected` on enter — no extra binding needed; verify and adapt (a `("enter", ...)` binding on the Screen would shadow the Input's submit — do NOT add one).

- [ ] **Step 4: Run tests to verify green + no TUI regressions**

Run: `uv run pytest tests/tui -q`
Expected: all pass.

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -q`
Expected: all pass (modulo the known canon flake).

- [ ] **Step 6: Commit**

```bash
git add novelizer/tui/settings_screen.py tests/tui/test_settings_screen.py
git commit -m "feat(tui): settings screen editing — typed edits, override clearing, live connection test"
```

---

### Task 7: Docs

**Files:**
- Modify: `README.md` (Configuration section: `:settings`, live-apply semantics)
- Modify: `docs/examples/config.example.toml` (one-line note that settings are editable in-app)

**Interfaces:** documentation only.

- [ ] **Step 1: Update README**

Append to the README Configuration section (match existing style):

```markdown
Inside the TUI, `:settings` opens a settings screen showing every setting
with its effective value and source layer (default / global / story / env).
Edits write straight to `config.toml` / `story.toml` — hand-edits to those
files while novelizer runs are picked up the same way. Cadence, voice, and
temperature changes apply live (voice and temperature affect the next
draft); endpoint and model changes are marked "restart required".
Generated chapters record the model, temperature, voice pack, and prose
profile they were written under.
```

- [ ] **Step 2: Update the example config**

Add one line to the header comment block of `docs/examples/config.example.toml`:

```toml
# All of these are also viewable/editable at runtime via :settings in the TUI.
```

- [ ] **Step 3: Verify**

Run: `uv run pytest -q`
Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add README.md docs/examples/config.example.toml
git commit -m "docs: settings screen, live-apply semantics, provenance"
```

---

## Post-plan notes

- Carried-over items deliberately NOT in this plan (tracked in the SDD ledger): moving app-managed state out of `config.toml` / comment-preserving writes (tomlkit or `XDG_STATE_HOME`), legacy flat story visibility in the picker after a declined migration, migration-flow dedupe in cli.py (`_maybe_migrate_flat`), `NOVELIZER_DB_PATH` dead-env cleanup in `tests/director/test_cli.py`, atomic 0600 writes in `toml_io`, wizard `q`-binding reachability. These are backlog, not Phase 3 scope — Phase 3 is the last planned phase of the settings spec.
- Editing `voice_pack` via the screen takes a filesystem path string; a voice-pack browser is out of scope (spec's voice UX beyond profile selection was never promised for this screen).
- The watcher polls at 1 Hz; inotify is deliberately avoided (portability, and the spec explicitly blessed "file watch or cheap mtime poll").
