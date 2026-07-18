# Settings & Configuration — Phase 1 (Foundation) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the flat `Settings` bag with a layered configuration system (defaults ← global ← story ← env) and make stories self-contained directories with derived paths.

**Architecture:** A new `novelizer/settings/` package holds pydantic models (`GlobalConfig`, `StoryConfig`, `EffectiveSettings`), TOML IO with friendly errors, a pure merge function (property-tested), and story-directory helpers (derived paths, creation, flat-layout migration). `runtime.py` and `director/cli.py` switch to `EffectiveSettings`; the old `novelizer/config.py` is deleted. The consumer-facing field names are unchanged, so agent code is untouched.

**Tech Stack:** Python ≥3.13 (stdlib `tomllib` for reading), `tomli-w` (new dep, TOML writing), pydantic v2, pydantic-settings (env layer only), Hypothesis, pytest (asyncio_mode auto), uv.

**Spec:** `docs/superpowers/specs/2026-07-18-settings-config-design.md` (this plan covers **Phase 1 only**; wizard/picker are Phase 2, TUI settings screen is Phase 3).

## Global Constraints

- Python ≥3.13; run everything via `uv run`.
- Env prefix is exactly `NOVELIZER_` (existing convention).
- Global config path: `$XDG_CONFIG_HOME/novelizer/config.toml`, falling back to `~/.config/novelizer/config.toml`.
- `llm_api_key` must never be accepted from `story.toml` — loading fails with `StoryConfigError`.
- Unknown keys in either TOML file warn (via `logging.getLogger("novelizer.settings")`), never crash.
- `db_path` / `chroma_path` are derived from the story directory — never read from TOML or env.
- Effective-settings field names must stay identical to today's `Settings` fields (agent code depends on them): `db_path`, `chroma_path`, `embed_model`, `llm_base_url`, `llm_api_key`, `author_model`, `author_temperature`, `agent_model`, `agent_temperature`, `author_interval`, `default_agent_interval`, `continuity_interval`, `structure_analyst_interval`, `projector_interval`, `voice_pack`, `prose_profile`.
- TDD red/green for every task; commit after every green.

---

### Task 1: `EffectiveSettings` model and package skeleton

**Files:**
- Create: `novelizer/settings/__init__.py`
- Create: `novelizer/settings/models.py`
- Test: `tests/settings/__init__.py` (empty), `tests/settings/test_models.py`
- Modify: `pyproject.toml` (add `tomli-w>=1.0.0` to `[project.dependencies]` — used from Task 2 on)

**Interfaces:**
- Consumes: nothing (first task).
- Produces: `EffectiveSettings` (frozen pydantic `BaseModel`; the 16 fields listed in Global Constraints with today's defaults, plus `story_title: str | None = None`, `default_stories_dir: str = "stories"`, `last_opened_story: str | None = None`); constants `STORY_OVERRIDABLE_KEYS: frozenset[str]`, `FORBIDDEN_STORY_KEYS: frozenset[str]`.

- [ ] **Step 1: Add dependency**

Run: `uv add "tomli-w>=1.0.0"`
Expected: `pyproject.toml` and `uv.lock` updated.

- [ ] **Step 2: Write the failing test**

`tests/settings/test_models.py`:

```python
import pydantic
import pytest

from novelizer.settings.models import (
    EffectiveSettings,
    STORY_OVERRIDABLE_KEYS,
    FORBIDDEN_STORY_KEYS,
)


def test_defaults_match_legacy_settings():
    s = EffectiveSettings()
    assert s.db_path == "stories/world.db"
    assert s.chroma_path == "stories/chroma"
    assert s.embed_model == "nomic-embed-text"
    assert s.llm_base_url == "http://localhost:8080/v1"
    assert s.llm_api_key == "not-needed"
    assert s.author_model == "local-model"
    assert s.author_temperature == 0.8
    assert s.agent_model == "local-model"
    assert s.agent_temperature == 0.7
    assert s.author_interval == 300
    assert s.default_agent_interval == 120
    assert s.continuity_interval == 900
    assert s.structure_analyst_interval == 180
    assert s.projector_interval == 0.5
    assert s.voice_pack.endswith("default.toml")
    assert s.prose_profile == "plain"
    assert s.story_title is None
    assert s.default_stories_dir == "stories"
    assert s.last_opened_story is None


def test_effective_settings_is_frozen():
    s = EffectiveSettings()
    with pytest.raises(pydantic.ValidationError):
        s.author_model = "other"


def test_key_sets():
    assert "llm_api_key" in FORBIDDEN_STORY_KEYS
    assert "llm_api_key" not in STORY_OVERRIDABLE_KEYS
    assert "llm_base_url" not in STORY_OVERRIDABLE_KEYS
    assert "voice_pack" in STORY_OVERRIDABLE_KEYS
    assert "embed_model" in STORY_OVERRIDABLE_KEYS
    assert STORY_OVERRIDABLE_KEYS <= set(EffectiveSettings.model_fields)
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/settings/test_models.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'novelizer.settings'`

- [ ] **Step 4: Write minimal implementation**

`novelizer/settings/models.py`:

```python
from __future__ import annotations

import importlib.resources

from pydantic import BaseModel, ConfigDict

_DEFAULT_VOICE_PACK = str(importlib.resources.files("novelizer.voices").joinpath("default.toml"))

# Settings a story.toml may override.
STORY_OVERRIDABLE_KEYS: frozenset[str] = frozenset({
    "voice_pack", "prose_profile",
    "author_model", "agent_model", "embed_model",
    "author_temperature", "agent_temperature",
    "author_interval", "default_agent_interval",
    "continuity_interval", "structure_analyst_interval", "projector_interval",
})

# Secrets: hard error if present in story.toml (stories are shareable).
FORBIDDEN_STORY_KEYS: frozenset[str] = frozenset({"llm_api_key"})


class EffectiveSettings(BaseModel):
    """Immutable merge of defaults <- global <- story <- env. Field names match
    the legacy Settings class; agent code and Runtime consume this unchanged."""

    model_config = ConfigDict(frozen=True)

    # Storage — derived from the story directory when one is given (see loader).
    db_path: str = "stories/world.db"
    chroma_path: str = "stories/chroma"
    embed_model: str = "nomic-embed-text"

    # OpenAI-compatible LLM endpoint (global-only in files)
    llm_base_url: str = "http://localhost:8080/v1"
    llm_api_key: str = "not-needed"
    author_model: str = "local-model"
    author_temperature: float = 0.8
    agent_model: str = "local-model"
    agent_temperature: float = 0.7

    # Cadence (seconds)
    author_interval: int = 300
    default_agent_interval: int = 120
    continuity_interval: int = 900
    structure_analyst_interval: int = 180
    projector_interval: float = 0.5

    # Voice
    voice_pack: str = _DEFAULT_VOICE_PACK
    prose_profile: str = "plain"

    # Story metadata / app-level
    story_title: str | None = None
    default_stories_dir: str = "stories"
    last_opened_story: str | None = None
```

`novelizer/settings/__init__.py`:

```python
from novelizer.settings.models import (
    EffectiveSettings,
    FORBIDDEN_STORY_KEYS,
    STORY_OVERRIDABLE_KEYS,
)

__all__ = [
    "EffectiveSettings",
    "FORBIDDEN_STORY_KEYS",
    "STORY_OVERRIDABLE_KEYS",
]
```

Also create empty `tests/settings/__init__.py`.

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/settings/test_models.py -v`
Expected: 3 passed

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock novelizer/settings tests/settings
git commit -m "feat(settings): EffectiveSettings model and key sets"
```

---

### Task 2: TOML IO with friendly errors

**Files:**
- Create: `novelizer/settings/toml_io.py`
- Test: `tests/settings/test_toml_io.py`
- Modify: `novelizer/settings/__init__.py` (export `TOMLFileError`, `load_toml_file`, `write_toml_file`)

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `TOMLFileError(Exception)`; `load_toml_file(path: Path) -> dict` (raises `TOMLFileError` naming file and parse location); `write_toml_file(path: Path, data: dict, mode: int | None = None) -> None` (creates parent dirs; chmods when `mode` given).

- [ ] **Step 1: Write the failing test**

`tests/settings/test_toml_io.py`:

```python
import os

import pytest

from novelizer.settings.toml_io import TOMLFileError, load_toml_file, write_toml_file


def test_round_trip(tmp_path):
    path = tmp_path / "sub" / "config.toml"
    write_toml_file(path, {"author_model": "m1", "author_temperature": 0.5})
    assert load_toml_file(path) == {"author_model": "m1", "author_temperature": 0.5}


def test_write_with_mode_0600(tmp_path):
    path = tmp_path / "config.toml"
    write_toml_file(path, {"llm_api_key": "secret"}, mode=0o600)
    assert os.stat(path).st_mode & 0o777 == 0o600


def test_invalid_toml_names_file_and_location(tmp_path):
    path = tmp_path / "bad.toml"
    path.write_text("author_model = \n")
    with pytest.raises(TOMLFileError) as exc:
        load_toml_file(path)
    assert str(path) in str(exc.value)
    assert "line" in str(exc.value)


def test_missing_file_raises(tmp_path):
    with pytest.raises(TOMLFileError) as exc:
        load_toml_file(tmp_path / "nope.toml")
    assert "nope.toml" in str(exc.value)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/settings/test_toml_io.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'novelizer.settings.toml_io'`

- [ ] **Step 3: Write minimal implementation**

`novelizer/settings/toml_io.py`:

```python
from __future__ import annotations

import os
import tomllib
from pathlib import Path

import tomli_w


class TOMLFileError(Exception):
    """A config file could not be read or parsed. Message names file and location."""


def load_toml_file(path: Path) -> dict:
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except FileNotFoundError:
        raise TOMLFileError(f"{path}: file not found") from None
    except tomllib.TOMLDecodeError as e:
        # tomllib messages include "at line N, column M"
        raise TOMLFileError(f"{path}: invalid TOML: {e}") from e


def write_toml_file(path: Path, data: dict, mode: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(tomli_w.dumps(data), encoding="utf-8")
    if mode is not None:
        os.chmod(path, mode)
```

Add to `novelizer/settings/__init__.py` imports and `__all__`: `TOMLFileError`, `load_toml_file`, `write_toml_file` (from `novelizer.settings.toml_io`).

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/settings/test_toml_io.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add novelizer/settings tests/settings/test_toml_io.py
git commit -m "feat(settings): TOML IO with friendly errors and 0600 writes"
```

---

### Task 3: Layer models and parsing (`GlobalConfig`, `StoryConfig`)

**Files:**
- Create: `novelizer/settings/layers.py`
- Test: `tests/settings/test_layers.py`
- Modify: `novelizer/settings/__init__.py` (export `GlobalConfig`, `StoryConfig`, `StoryConfigError`, `parse_global`, `parse_story`, `global_config_path`)

**Interfaces:**
- Consumes: `STORY_OVERRIDABLE_KEYS`, `FORBIDDEN_STORY_KEYS` from Task 1.
- Produces:
  - `GlobalConfig(BaseModel)` — every field `Optional`, default `None`: the 12 story-overridable keys plus `llm_base_url`, `llm_api_key`, `default_stories_dir`, `last_opened_story`.
  - `StoryConfig(BaseModel)` — every field `Optional`, default `None`: the 12 story-overridable keys plus `title`.
  - `StoryConfigError(Exception)`.
  - `parse_global(data: dict, source: str) -> GlobalConfig` — warns on unknown keys.
  - `parse_story(data: dict, source: str) -> StoryConfig` — raises `StoryConfigError` on forbidden keys, warns on unknown/global-only keys.
  - `global_config_path() -> Path` — XDG-respecting.

- [ ] **Step 1: Write the failing test**

`tests/settings/test_layers.py`:

```python
import logging
from pathlib import Path

import pytest

from novelizer.settings.layers import (
    GlobalConfig,
    StoryConfig,
    StoryConfigError,
    global_config_path,
    parse_global,
    parse_story,
)


def test_parse_global_known_keys():
    cfg = parse_global({"llm_base_url": "http://h:1/v1", "author_model": "m"}, source="g.toml")
    assert cfg.llm_base_url == "http://h:1/v1"
    assert cfg.author_model == "m"
    assert cfg.llm_api_key is None  # unset stays None so it doesn't shadow defaults


def test_parse_global_warns_on_unknown_key(caplog):
    with caplog.at_level(logging.WARNING, logger="novelizer.settings"):
        parse_global({"db_path": "x", "frobnicate": 1}, source="g.toml")
    text = caplog.text
    assert "db_path" in text and "frobnicate" in text and "g.toml" in text


def test_parse_story_rejects_api_key():
    with pytest.raises(StoryConfigError) as exc:
        parse_story({"llm_api_key": "sk-real"}, source="story.toml")
    assert "llm_api_key" in str(exc.value)
    assert "story.toml" in str(exc.value)


def test_parse_story_warns_on_global_only_key(caplog):
    with caplog.at_level(logging.WARNING, logger="novelizer.settings"):
        cfg = parse_story({"llm_base_url": "http://h:1/v1", "prose_profile": "lush"}, source="s.toml")
    assert cfg.prose_profile == "lush"
    assert "llm_base_url" in caplog.text


def test_parse_story_title():
    cfg = parse_story({"title": "My Novel"}, source="s.toml")
    assert cfg.title == "My Novel"


def test_global_config_path_respects_xdg(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert global_config_path() == tmp_path / "novelizer" / "config.toml"


def test_global_config_path_defaults_to_dot_config(monkeypatch):
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    p = global_config_path()
    assert p == Path.home() / ".config" / "novelizer" / "config.toml"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/settings/test_layers.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'novelizer.settings.layers'`

- [ ] **Step 3: Write minimal implementation**

`novelizer/settings/layers.py`:

```python
from __future__ import annotations

import logging
import os
from pathlib import Path

from pydantic import BaseModel

from novelizer.settings.models import FORBIDDEN_STORY_KEYS

logger = logging.getLogger("novelizer.settings")


class StoryConfigError(Exception):
    """story.toml contains keys that must never appear there (secrets)."""


class GlobalConfig(BaseModel):
    """~/.config/novelizer/config.toml. All fields optional: None means 'unset,
    fall through to built-in defaults'."""

    llm_base_url: str | None = None
    llm_api_key: str | None = None
    default_stories_dir: str | None = None
    last_opened_story: str | None = None

    voice_pack: str | None = None
    prose_profile: str | None = None
    author_model: str | None = None
    agent_model: str | None = None
    embed_model: str | None = None
    author_temperature: float | None = None
    agent_temperature: float | None = None
    author_interval: int | None = None
    default_agent_interval: int | None = None
    continuity_interval: int | None = None
    structure_analyst_interval: int | None = None
    projector_interval: float | None = None


class StoryConfig(BaseModel):
    """story.toml inside a story directory. Overrides global defaults."""

    title: str | None = None

    voice_pack: str | None = None
    prose_profile: str | None = None
    author_model: str | None = None
    agent_model: str | None = None
    embed_model: str | None = None
    author_temperature: float | None = None
    agent_temperature: float | None = None
    author_interval: int | None = None
    default_agent_interval: int | None = None
    continuity_interval: int | None = None
    structure_analyst_interval: int | None = None
    projector_interval: float | None = None


def global_config_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "novelizer" / "config.toml"


def parse_global(data: dict, source: str) -> GlobalConfig:
    known = set(GlobalConfig.model_fields)
    for key in sorted(data.keys() - known):
        logger.warning("%s: unknown setting %r ignored", source, key)
    return GlobalConfig(**{k: v for k, v in data.items() if k in known})


def parse_story(data: dict, source: str) -> StoryConfig:
    forbidden = sorted(data.keys() & FORBIDDEN_STORY_KEYS)
    if forbidden:
        raise StoryConfigError(
            f"{source}: {forbidden} must not appear in story.toml — stories are shareable; "
            f"secrets belong in the global config ({global_config_path()})"
        )
    known = set(StoryConfig.model_fields)
    for key in sorted(data.keys() - known):
        logger.warning("%s: unknown or global-only setting %r ignored", source, key)
    return StoryConfig(**{k: v for k, v in data.items() if k in known})
```

Add to `novelizer/settings/__init__.py` imports and `__all__`: `GlobalConfig`, `StoryConfig`, `StoryConfigError`, `global_config_path`, `parse_global`, `parse_story` (from `novelizer.settings.layers`).

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/settings/test_layers.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add novelizer/settings tests/settings/test_layers.py
git commit -m "feat(settings): GlobalConfig/StoryConfig parsing with warnings and secret rejection"
```

---

### Task 4: Story directories — derived paths, creation, migration

**Files:**
- Create: `novelizer/settings/story_dir.py`
- Test: `tests/settings/test_story_dir.py`
- Modify: `novelizer/settings/__init__.py` (export `StoryDirectory`, `is_story_dir`, `create_story`, `migrate_flat_layout`)

**Interfaces:**
- Consumes: `write_toml_file` (Task 2).
- Produces:
  - `StoryDirectory` — frozen dataclass with `root: Path`; properties `db_path -> Path` (`root/"world.db"`), `chroma_path -> Path` (`root/"chroma"`), `story_toml -> Path` (`root/"story.toml"`).
  - `is_story_dir(path: Path) -> bool` — true if `story.toml` or `world.db` exists inside.
  - `create_story(root: Path, title: str) -> StoryDirectory` — mkdirs, writes `story.toml` with the title.
  - `migrate_flat_layout(stories_root: Path, story_name: str = "default") -> StoryDirectory` — moves flat `world.db` (+ `chroma/` if present) into `stories_root/story_name/`, writes `story.toml`.

- [ ] **Step 1: Write the failing test**

`tests/settings/test_story_dir.py`:

```python
import pytest

from novelizer.settings.story_dir import (
    StoryDirectory,
    create_story,
    is_story_dir,
    migrate_flat_layout,
)
from novelizer.settings.toml_io import load_toml_file


def test_derived_paths(tmp_path):
    sd = StoryDirectory(root=tmp_path / "novel")
    assert sd.db_path == tmp_path / "novel" / "world.db"
    assert sd.chroma_path == tmp_path / "novel" / "chroma"
    assert sd.story_toml == tmp_path / "novel" / "story.toml"


def test_is_story_dir(tmp_path):
    assert not is_story_dir(tmp_path)
    (tmp_path / "story.toml").write_text("")
    assert is_story_dir(tmp_path)


def test_is_story_dir_with_only_db(tmp_path):
    (tmp_path / "world.db").write_bytes(b"")
    assert is_story_dir(tmp_path)


def test_create_story(tmp_path):
    sd = create_story(tmp_path / "my-novel", title="My Novel")
    assert sd.root.is_dir()
    assert load_toml_file(sd.story_toml) == {"title": "My Novel"}
    assert is_story_dir(sd.root)


def test_migrate_flat_layout(tmp_path):
    (tmp_path / "world.db").write_bytes(b"dbdata")
    (tmp_path / "chroma").mkdir()
    (tmp_path / "chroma" / "seg").write_bytes(b"x")
    sd = migrate_flat_layout(tmp_path)
    assert sd.root == tmp_path / "default"
    assert sd.db_path.read_bytes() == b"dbdata"
    assert (sd.chroma_path / "seg").read_bytes() == b"x"
    assert not (tmp_path / "world.db").exists()
    assert not (tmp_path / "chroma").exists()
    assert load_toml_file(sd.story_toml) == {"title": "default"}


def test_migrate_flat_layout_without_chroma(tmp_path):
    (tmp_path / "world.db").write_bytes(b"dbdata")
    sd = migrate_flat_layout(tmp_path)
    assert sd.db_path.read_bytes() == b"dbdata"
    assert not sd.chroma_path.exists()


def test_migrate_flat_layout_nothing_to_migrate(tmp_path):
    with pytest.raises(FileNotFoundError):
        migrate_flat_layout(tmp_path)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/settings/test_story_dir.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'novelizer.settings.story_dir'`

- [ ] **Step 3: Write minimal implementation**

`novelizer/settings/story_dir.py`:

```python
from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from novelizer.settings.toml_io import write_toml_file


@dataclass(frozen=True)
class StoryDirectory:
    """A self-contained story folder. All storage paths derive from `root`."""

    root: Path

    @property
    def db_path(self) -> Path:
        return self.root / "world.db"

    @property
    def chroma_path(self) -> Path:
        return self.root / "chroma"

    @property
    def story_toml(self) -> Path:
        return self.root / "story.toml"


def is_story_dir(path: Path) -> bool:
    return (path / "story.toml").exists() or (path / "world.db").exists()


def create_story(root: Path, title: str) -> StoryDirectory:
    sd = StoryDirectory(root=root)
    root.mkdir(parents=True, exist_ok=True)
    write_toml_file(sd.story_toml, {"title": title})
    return sd


def migrate_flat_layout(stories_root: Path, story_name: str = "default") -> StoryDirectory:
    """Move a legacy flat layout (stories/world.db, stories/chroma) into a
    proper story directory (stories/<story_name>/)."""
    flat_db = stories_root / "world.db"
    if not flat_db.exists():
        raise FileNotFoundError(f"{flat_db}: no flat-layout story to migrate")
    sd = StoryDirectory(root=stories_root / story_name)
    sd.root.mkdir(parents=True, exist_ok=True)
    shutil.move(str(flat_db), str(sd.db_path))
    flat_chroma = stories_root / "chroma"
    if flat_chroma.exists():
        shutil.move(str(flat_chroma), str(sd.chroma_path))
    write_toml_file(sd.story_toml, {"title": story_name})
    return sd
```

Add to `novelizer/settings/__init__.py` imports and `__all__`: `StoryDirectory`, `is_story_dir`, `create_story`, `migrate_flat_layout` (from `novelizer.settings.story_dir`).

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/settings/test_story_dir.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add novelizer/settings tests/settings/test_story_dir.py
git commit -m "feat(settings): StoryDirectory with derived paths, creation, flat-layout migration"
```

---

### Task 5: Merge + env layer → `load_effective_settings` (property-tested)

**Files:**
- Create: `novelizer/settings/loader.py`
- Test: `tests/settings/test_loader.py`
- Modify: `novelizer/settings/__init__.py` (export `EnvOverrides`, `build_effective`, `load_effective_settings`)

**Interfaces:**
- Consumes: `EffectiveSettings` (Task 1), `load_toml_file`/`TOMLFileError` (Task 2), `GlobalConfig`/`StoryConfig`/`parse_global`/`parse_story`/`global_config_path` (Task 3), `StoryDirectory` (Task 4).
- Produces:
  - `EnvOverrides(BaseSettings)` — env prefix `NOVELIZER_`, all `GlobalConfig` fields as optionals, `extra="ignore"`.
  - `build_effective(global_cfg: GlobalConfig, story_cfg: StoryConfig, env: EnvOverrides, story_dir: StoryDirectory | None = None) -> EffectiveSettings` — **pure**, no IO.
  - `load_effective_settings(story_dir: StoryDirectory | None = None, global_path: Path | None = None) -> EffectiveSettings` — reads files (missing files = empty layer), applies env, delegates to `build_effective`.

- [ ] **Step 1: Write the failing example-based tests**

`tests/settings/test_loader.py` (first half):

```python
from pathlib import Path

from novelizer.settings.layers import GlobalConfig, StoryConfig
from novelizer.settings.loader import EnvOverrides, build_effective, load_effective_settings
from novelizer.settings.story_dir import StoryDirectory, create_story
from novelizer.settings.toml_io import write_toml_file


def _env(**kwargs) -> EnvOverrides:
    # _env_file=None so a developer's real .env can't leak into tests
    return EnvOverrides(_env_file=None, **kwargs)


def test_precedence_env_over_story_over_global():
    eff = build_effective(
        GlobalConfig(author_model="g", agent_model="g", prose_profile="g"),
        StoryConfig(agent_model="s", prose_profile="s"),
        _env(prose_profile="e"),
    )
    assert eff.author_model == "g"   # global beats default
    assert eff.agent_model == "s"    # story beats global
    assert eff.prose_profile == "e"  # env beats story
    assert eff.embed_model == "nomic-embed-text"  # untouched default survives


def test_story_dir_forces_derived_paths(tmp_path):
    sd = StoryDirectory(root=tmp_path / "novel")
    eff = build_effective(GlobalConfig(), StoryConfig(), _env(), story_dir=sd)
    assert eff.db_path == str(sd.db_path)
    assert eff.chroma_path == str(sd.chroma_path)


def test_story_title_carried():
    eff = build_effective(GlobalConfig(), StoryConfig(title="My Novel"), _env())
    assert eff.story_title == "My Novel"


def test_load_effective_settings_reads_files(tmp_path, monkeypatch):
    monkeypatch.delenv("NOVELIZER_AUTHOR_MODEL", raising=False)
    gpath = tmp_path / "config.toml"
    write_toml_file(gpath, {"author_model": "global-m", "author_temperature": 0.3})
    sd = create_story(tmp_path / "novel", title="N")
    write_toml_file(sd.story_toml, {"title": "N", "author_temperature": 0.9})
    eff = load_effective_settings(story_dir=sd, global_path=gpath)
    assert eff.author_model == "global-m"
    assert eff.author_temperature == 0.9
    assert eff.db_path == str(sd.db_path)


def test_load_effective_settings_env_wins(tmp_path, monkeypatch):
    gpath = tmp_path / "config.toml"
    write_toml_file(gpath, {"author_model": "global-m"})
    monkeypatch.setenv("NOVELIZER_AUTHOR_MODEL", "env-m")
    eff = load_effective_settings(global_path=gpath)
    assert eff.author_model == "env-m"


def test_load_effective_settings_missing_files_ok(tmp_path):
    eff = load_effective_settings(global_path=tmp_path / "absent.toml")
    assert eff.author_model == "local-model"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/settings/test_loader.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'novelizer.settings.loader'`

- [ ] **Step 3: Write minimal implementation**

`novelizer/settings/loader.py`:

```python
from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

from novelizer.settings.layers import (
    GlobalConfig,
    StoryConfig,
    global_config_path,
    parse_global,
    parse_story,
)
from novelizer.settings.models import EffectiveSettings
from novelizer.settings.story_dir import StoryDirectory
from novelizer.settings.toml_io import load_toml_file


class EnvOverrides(BaseSettings):
    """NOVELIZER_* environment variables — the highest-precedence layer."""

    model_config = SettingsConfigDict(env_prefix="NOVELIZER_", env_file=".env", extra="ignore")

    llm_base_url: str | None = None
    llm_api_key: str | None = None
    default_stories_dir: str | None = None
    last_opened_story: str | None = None

    voice_pack: str | None = None
    prose_profile: str | None = None
    author_model: str | None = None
    agent_model: str | None = None
    embed_model: str | None = None
    author_temperature: float | None = None
    agent_temperature: float | None = None
    author_interval: int | None = None
    default_agent_interval: int | None = None
    continuity_interval: int | None = None
    structure_analyst_interval: int | None = None
    projector_interval: float | None = None


def build_effective(
    global_cfg: GlobalConfig,
    story_cfg: StoryConfig,
    env: EnvOverrides,
    story_dir: StoryDirectory | None = None,
) -> EffectiveSettings:
    """Pure merge: defaults <- global <- story <- env; storage paths derived
    from the story directory when one is given."""
    merged: dict = {}
    merged.update(global_cfg.model_dump(exclude_none=True))
    merged.update(story_cfg.model_dump(exclude_none=True, exclude={"title"}))
    merged.update(env.model_dump(exclude_none=True))
    if story_dir is not None:
        merged["db_path"] = str(story_dir.db_path)
        merged["chroma_path"] = str(story_dir.chroma_path)
    return EffectiveSettings(story_title=story_cfg.title, **merged)


def load_effective_settings(
    story_dir: StoryDirectory | None = None,
    global_path: Path | None = None,
) -> EffectiveSettings:
    gpath = global_path if global_path is not None else global_config_path()
    global_cfg = parse_global(load_toml_file(gpath), source=str(gpath)) if gpath.exists() else GlobalConfig()
    story_cfg = StoryConfig()
    if story_dir is not None and story_dir.story_toml.exists():
        story_cfg = parse_story(load_toml_file(story_dir.story_toml), source=str(story_dir.story_toml))
    return build_effective(global_cfg, story_cfg, EnvOverrides(), story_dir=story_dir)
```

Add to `novelizer/settings/__init__.py` imports and `__all__`: `EnvOverrides`, `build_effective`, `load_effective_settings` (from `novelizer.settings.loader`).

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/settings/test_loader.py -v`
Expected: 6 passed

- [ ] **Step 5: Add the Hypothesis property test**

Append to `tests/settings/test_loader.py`:

```python
from hypothesis import given
from hypothesis import strategies as st

# Representative overridable keys, one per value type.
_PROPERTY_KEYS = {
    "agent_model": st.text(min_size=1, max_size=12),
    "prose_profile": st.text(min_size=1, max_size=12),
    "author_temperature": st.floats(0.0, 2.0, allow_nan=False),
    "author_interval": st.integers(1, 100_000),
}

_layer = st.fixed_dictionaries({}, optional=_PROPERTY_KEYS)


@given(global_d=_layer, story_d=_layer, env_d=_layer)
def test_precedence_property(global_d, story_d, env_d):
    """For every key: env > story > global > built-in default."""
    defaults = EffectiveSettings()
    eff = build_effective(
        GlobalConfig(**global_d), StoryConfig(**story_d), _env(**env_d)
    )
    for key in _PROPERTY_KEYS:
        expected = env_d.get(key, story_d.get(key, global_d.get(key, getattr(defaults, key))))
        assert getattr(eff, key) == expected
```

- [ ] **Step 6: Run the full settings suite**

Run: `uv run pytest tests/settings -v`
Expected: all passed (property test included)

- [ ] **Step 7: Commit**

```bash
git add novelizer/settings tests/settings/test_loader.py
git commit -m "feat(settings): layered merge with env overrides, property-tested precedence"
```

---

### Task 6: Wire into CLI and Runtime; delete legacy `config.py`

**Files:**
- Modify: `novelizer/director/cli.py` (lines 1–51: imports, `cli` group, add `_resolve_story`)
- Modify: `novelizer/runtime.py` (line 3: import; line 22: type annotation)
- Modify: `tests/test_runtime.py`, `tests/agents/test_author_live_llm.py`, `tests/tui/test_app_commands.py`, `tests/tui/test_app_resilience.py`, `tests/tui/test_app_smoke.py`, `tests/tui/test_app_layout.py` (import line only)
- Delete: `novelizer/config.py`, `tests/test_config.py` (superseded by `tests/settings/`)
- Test: `tests/director/test_resolve_story.py`

**Interfaces:**
- Consumes: everything from Tasks 1–5 via `novelizer.settings`.
- Produces: `_resolve_story(story_path: str | None, confirm=click.confirm) -> StoryDirectory` in `novelizer/director/cli.py`; CLI gains `--story PATH` option on the top-level group.

- [ ] **Step 1: Write the failing test for story resolution**

`tests/director/test_resolve_story.py` (create `tests/director/__init__.py` empty if missing):

```python
from novelizer.director.cli import _resolve_story
from novelizer.settings import create_story


def test_explicit_story_path(tmp_path):
    sd = _resolve_story(str(tmp_path / "novel"), stories_root=tmp_path / "stories")
    assert sd.root == tmp_path / "novel"


def test_flat_layout_migrates_when_confirmed(tmp_path):
    root = tmp_path / "stories"
    root.mkdir()
    (root / "world.db").write_bytes(b"db")
    sd = _resolve_story(None, stories_root=root, confirm=lambda *a, **k: True)
    assert sd.root == root / "default"
    assert sd.db_path.read_bytes() == b"db"


def test_flat_layout_kept_when_declined(tmp_path):
    root = tmp_path / "stories"
    root.mkdir()
    (root / "world.db").write_bytes(b"db")
    sd = _resolve_story(None, stories_root=root, confirm=lambda *a, **k: False)
    # Declining keeps legacy paths working: the root itself acts as the story dir.
    assert sd.root == root
    assert sd.db_path == root / "world.db"


def test_existing_default_story_used(tmp_path):
    root = tmp_path / "stories"
    create_story(root / "default", title="default")
    sd = _resolve_story(None, stories_root=root)
    assert sd.root == root / "default"


def test_fresh_install_creates_default_story(tmp_path):
    root = tmp_path / "stories"
    sd = _resolve_story(None, stories_root=root)
    assert sd.root == root / "default"
    assert sd.story_toml.exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/director/test_resolve_story.py -v`
Expected: FAIL with `ImportError: cannot import name '_resolve_story'`

- [ ] **Step 3: Implement CLI wiring**

In `novelizer/director/cli.py`, replace the import `from novelizer.config import Settings` with:

```python
from pathlib import Path

from novelizer.settings import (
    EffectiveSettings,
    StoryDirectory,
    create_story,
    is_story_dir,
    load_effective_settings,
    migrate_flat_layout,
)
```

Add `_resolve_story` above the `cli` group:

```python
def _resolve_story(
    story_path: str | None,
    stories_root: Path = Path("stories"),
    confirm=click.confirm,
) -> StoryDirectory:
    """Pick the story directory: explicit --story wins; else migrate/reuse/create
    the default story under stories_root."""
    if story_path:
        return StoryDirectory(root=Path(story_path))
    if (stories_root / "world.db").exists():
        if confirm(
            f"Found legacy flat story at {stories_root}/world.db. "
            f"Migrate it into {stories_root}/default/?",
            default=True,
        ):
            return migrate_flat_layout(stories_root)
        return StoryDirectory(root=stories_root)  # legacy paths keep working
    default = stories_root / "default"
    if is_story_dir(default):
        return StoryDirectory(root=default)
    return create_story(default, title="default")
```

Replace the `cli` group definition:

```python
@click.group(invoke_without_command=True)
@click.option("--story", "story_path", default=None, type=click.Path(), help="Path to a story directory.")
@click.pass_context
def cli(ctx, story_path: str | None):
    ctx.ensure_object(dict)
    story = _resolve_story(story_path)
    ctx.obj["settings"] = load_effective_settings(story_dir=story)
    if ctx.invoked_subcommand is None:
        _launch_tui(ctx.obj["settings"])
```

Update `_launch_tui`'s annotation to `settings: EffectiveSettings`.

In `novelizer/runtime.py` line 3, replace `from novelizer.config import Settings` with `from novelizer.settings import EffectiveSettings`, and update the constructor annotation (line 22) to `settings: EffectiveSettings`. `EffectiveSettings.db_path` is a `str` like before; no other `runtime.py` changes.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/director/test_resolve_story.py -v`
Expected: 5 passed

- [ ] **Step 5: Migrate remaining consumers and delete legacy config**

In each of `tests/test_runtime.py`, `tests/agents/test_author_live_llm.py`, `tests/tui/test_app_commands.py`, `tests/tui/test_app_resilience.py`, `tests/tui/test_app_smoke.py`, `tests/tui/test_app_layout.py`, replace the line

```python
from novelizer.config import Settings
```

with

```python
from novelizer.settings import EffectiveSettings as Settings
```

(Call sites like `Settings(db_path=str(tmp_path / "w.db"))` keep working — field names and defaults are identical.)

Then:

```bash
git rm novelizer/config.py tests/test_config.py
grep -rn "novelizer.config" novelizer tests  # must print nothing
```

- [ ] **Step 6: Run the full test suite**

Run: `uv run pytest`
Expected: all tests pass; no import errors anywhere.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "feat(settings): wire layered settings into CLI/Runtime, drop legacy config.py"
```

---

### Task 7: Documented example config + README

**Files:**
- Create: `docs/examples/config.example.toml`
- Modify: `README.md` (add a "Configuration" section; adjust as fits the existing README structure)

**Interfaces:**
- Consumes: field names from Task 1.
- Produces: user-facing documentation only.

- [ ] **Step 1: Write the example config**

`docs/examples/config.example.toml`:

```toml
# Novelizer global configuration.
# Location: ~/.config/novelizer/config.toml  (or $XDG_CONFIG_HOME/novelizer/config.toml)
#
# Layering: built-in defaults <- this file <- story.toml <- NOVELIZER_* env vars.
# Any setting here can be overridden per story in <story>/story.toml, EXCEPT:
#   llm_base_url, llm_api_key, default_stories_dir, last_opened_story (global-only;
#   llm_api_key in a story.toml is an error — stories are shareable).

# --- LLM endpoint (OpenAI-compatible) ---
llm_base_url = "http://localhost:8080/v1"
# llm_api_key = "sk-..."        # only if your endpoint needs one; file is chmod 0600

# --- Models ---
author_model = "local-model"
agent_model = "local-model"
embed_model = "nomic-embed-text"

# --- Generation ---
author_temperature = 0.8
agent_temperature = 0.7

# --- Agent cadence (seconds) ---
author_interval = 300
default_agent_interval = 120
continuity_interval = 900
structure_analyst_interval = 180
projector_interval = 0.5

# --- Voice ---
# voice_pack = "/path/to/pack.toml"   # defaults to the shipped default pack
prose_profile = "plain"

# --- App ---
default_stories_dir = "stories"
```

- [ ] **Step 2: Add a Configuration section to README.md**

Content to include (merge into the README's existing style):

```markdown
## Configuration

Settings layer in this order (later wins): built-in defaults ← global config ←
story config ← `NOVELIZER_*` environment variables.

- **Global:** `~/.config/novelizer/config.toml` — see
  `docs/examples/config.example.toml` for a documented example.
- **Per story:** each story is a self-contained directory
  (`world.db`, `chroma/`, `story.toml`). `story.toml` can override voice,
  models, temperatures, and cadence for that story. Secrets are never valid
  in `story.toml`.
- **Env:** any setting, e.g. `NOVELIZER_AUTHOR_MODEL=qwen3`.

Open a specific story with `novelizer --story path/to/story/`. With no
`--story`, novelizer uses `stories/default/` (offering a one-time migration
if it finds a legacy flat `stories/world.db`).
```

- [ ] **Step 3: Verify docs don't break anything**

Run: `uv run pytest`
Expected: all tests still pass.

- [ ] **Step 4: Commit**

```bash
git add docs/examples/config.example.toml README.md
git commit -m "docs: documented example config.toml and README configuration section"
```

---

## Post-plan notes

- The stale root `.env.example` is **untracked** in the main checkout (never committed), so there is nothing to `git rm`; it should simply be deleted from the working directory of the main checkout.
- Phase 2 (first-run wizard, story picker, connectivity test) and Phase 3 (TUI settings screen, live-apply, provenance stamping) get their own plans once this lands. `EnvOverrides`' `last_opened_story` / `default_stories_dir` fields and `write_toml_file(..., mode=0o600)` are the hooks Phase 2 builds on.
- Legacy `NOVELIZER_DB_PATH` env var no longer exists (paths are derived). If someone was using it, `--story` replaces it.
