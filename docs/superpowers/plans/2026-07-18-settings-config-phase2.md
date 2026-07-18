# Settings & Configuration — Phase 2 (Entry UX) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** First-run setup wizard (endpoint + live connectivity test + model picker), story picker/creation on launch, and story resolution that honors `default_stories_dir`, persists a declined migration, validates `--story`, and records `last_opened_story`.

**Architecture:** The wizard and picker are small standalone Textual apps (`novelizer/tui/setup_wizard.py`, `novelizer/tui/story_picker.py`) that run *before* the main `NovelizerApp` boots — the main app keeps its "constructed with a started Runtime" contract, untouched. Each app is a thin shell over TUI-independent cores in `novelizer/settings/` (`setup_core.py` for probing/config assembly, `discovery.py` for story listing/ordering/slugs, `global_store.py` for atomic 0600 config writes), satisfying the spec's CLI-reuse requirement. `director/cli.py` orchestrates: missing global config → wizard; no `--story` → picker; then the existing boot path.

**Tech Stack:** Python ≥3.13, Textual 5.3 (App.run / run_test pilot), httpx (promoted to a direct dependency; already installed transitively), pydantic v2, pytest + pytest-asyncio (asyncio_mode auto), uv.

**Spec:** `docs/superpowers/specs/2026-07-18-settings-config-design.md` §4 (wizard), §5 (picker), §8–9 (secrets/errors). Phase 1 (merged) provides `novelizer.settings`: `EffectiveSettings`, `GlobalConfig`, `StoryConfig`, `EnvOverrides`, `load_effective_settings`, `global_config_path`, `load_toml_file`, `write_toml_file`, `StoryDirectory`, `is_story_dir`, `create_story`, `migrate_flat_layout`, `TOMLFileError`, `StoryConfigError`.

## Global Constraints

- Python ≥3.13; run everything via `uv run`.
- Global config writes are `0600` (it may hold `llm_api_key`).
- The wizard core and picker core must be importable and testable without Textual.
- Connectivity probe: `GET {llm_base_url}/models` (OpenAI-compatible; `llm_base_url` already ends in `/v1`), `Authorization: Bearer <key>`, 5-second timeout, never raises — returns a result object.
- Story resolution precedence (headless subcommands): `--story` (validated) → valid `last_opened_story` → legacy-flat migration flow → `stories_root/default` (reuse or create).
- A declined flat-layout migration is persisted as `suppress_flat_migration_prompt = true` in the global config and never re-prompted.
- `--story` pointing at a non-story path is a friendly `click.ClickException`, not silent creation.
- Every `NOVELIZER_*`-visible field exists on `GlobalConfig`, `EnvOverrides`, and `EffectiveSettings` alike (locked by Phase 1's invariant tests — update them when adding fields).
- TDD red/green for every task; commit after every green.

---

### Task 1: `suppress_flat_migration_prompt` field across the three models

**Files:**
- Modify: `novelizer/settings/models.py` (EffectiveSettings)
- Modify: `novelizer/settings/layers.py` (GlobalConfig)
- Modify: `novelizer/settings/loader.py` (EnvOverrides)
- Test: `tests/settings/test_layers.py` (extend the existing invariant tests)

**Interfaces:**
- Consumes: Phase 1 models.
- Produces: `suppress_flat_migration_prompt: bool = False` on `EffectiveSettings`; `suppress_flat_migration_prompt: bool | None = None` on `GlobalConfig` and `EnvOverrides`.

- [ ] **Step 1: Write the failing test**

In `tests/settings/test_layers.py`, find the existing invariant test asserting the `GlobalConfig` field set and change its expected set to include the new key (exact current assertion may differ cosmetically — keep its style):

```python
def test_global_config_fields_are_overridable_plus_global_only():
    assert set(GlobalConfig.model_fields) == STORY_OVERRIDABLE_KEYS | {
        "llm_base_url",
        "llm_api_key",
        "default_stories_dir",
        "last_opened_story",
        "suppress_flat_migration_prompt",
    }
```

Add one new test:

```python
def test_suppress_flat_migration_prompt_defaults_false():
    from novelizer.settings import EffectiveSettings

    assert EffectiveSettings().suppress_flat_migration_prompt is False
```

(The existing `EnvOverrides == GlobalConfig` invariant test needs no edit — it will fail until `EnvOverrides` gains the field too, which is exactly the drift-guard working.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/settings/test_layers.py -v`
Expected: FAIL — the field-set assertion and the new default test both fail.

- [ ] **Step 3: Implement**

Add to `GlobalConfig` in `novelizer/settings/layers.py` (with the other global-only fields):

```python
    suppress_flat_migration_prompt: bool | None = None
```

Add the same line to `EnvOverrides` in `novelizer/settings/loader.py`.

Add to `EffectiveSettings` in `novelizer/settings/models.py` (in the "Story metadata / app-level" section):

```python
    suppress_flat_migration_prompt: bool = False
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/settings -v`
Expected: all pass (including untouched invariant tests).

- [ ] **Step 5: Commit**

```bash
git add novelizer/settings tests/settings/test_layers.py
git commit -m "feat(settings): suppress_flat_migration_prompt field for persisted migration decline"
```

---

### Task 2: `global_store.py` — write/update the global config at 0600

**Files:**
- Create: `novelizer/settings/global_store.py`
- Test: `tests/settings/test_global_store.py`
- Modify: `novelizer/settings/__init__.py` (export `write_global_config`, `update_global_config`)

**Interfaces:**
- Consumes: `global_config_path`, `load_toml_file`, `write_toml_file` (Phase 1).
- Produces:
  - `write_global_config(data: dict, path: Path | None = None) -> Path` — writes TOML at `path or global_config_path()`, mode `0600`, returns the path.
  - `update_global_config(path: Path | None = None, **changes) -> dict` — read-modify-write: loads existing file (missing = `{}`), applies `changes` (a value of `None` removes the key), writes back at `0600`, returns the resulting dict. Unknown keys already in the file are preserved verbatim.

- [ ] **Step 1: Write the failing test**

`tests/settings/test_global_store.py`:

```python
import os

from novelizer.settings.global_store import update_global_config, write_global_config
from novelizer.settings.toml_io import load_toml_file, write_toml_file


def test_write_global_config_0600(tmp_path):
    path = tmp_path / "cfg" / "config.toml"
    returned = write_global_config({"llm_api_key": "sk-x"}, path=path)
    assert returned == path
    assert os.stat(path).st_mode & 0o777 == 0o600
    assert load_toml_file(path) == {"llm_api_key": "sk-x"}


def test_update_creates_missing_file(tmp_path):
    path = tmp_path / "config.toml"
    result = update_global_config(path=path, last_opened_story="/s/novel")
    assert result == {"last_opened_story": "/s/novel"}
    assert load_toml_file(path) == {"last_opened_story": "/s/novel"}
    assert os.stat(path).st_mode & 0o777 == 0o600


def test_update_preserves_unknown_keys(tmp_path):
    path = tmp_path / "config.toml"
    write_toml_file(path, {"future_key": "kept", "author_model": "m"})
    result = update_global_config(path=path, author_model="m2")
    assert result == {"future_key": "kept", "author_model": "m2"}


def test_update_none_removes_key(tmp_path):
    path = tmp_path / "config.toml"
    write_toml_file(path, {"last_opened_story": "/gone", "author_model": "m"})
    result = update_global_config(path=path, last_opened_story=None)
    assert result == {"author_model": "m"}


def test_default_path_is_global_config_path(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    returned = write_global_config({"author_model": "m"})
    assert returned == tmp_path / "novelizer" / "config.toml"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/settings/test_global_store.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'novelizer.settings.global_store'`

- [ ] **Step 3: Implement**

`novelizer/settings/global_store.py`:

```python
from __future__ import annotations

from pathlib import Path

from novelizer.settings.layers import global_config_path
from novelizer.settings.toml_io import load_toml_file, write_toml_file


def write_global_config(data: dict, path: Path | None = None) -> Path:
    """Write the global config file. Always 0600: it may hold llm_api_key."""
    target = path if path is not None else global_config_path()
    write_toml_file(target, data, mode=0o600)
    return target


def update_global_config(path: Path | None = None, **changes) -> dict:
    """Read-modify-write single keys (e.g. last_opened_story). A value of None
    removes the key. Unknown keys already in the file are preserved."""
    target = path if path is not None else global_config_path()
    data = load_toml_file(target) if target.exists() else {}
    for key, value in changes.items():
        if value is None:
            data.pop(key, None)
        else:
            data[key] = value
    write_global_config(data, path=target)
    return data
```

Add to `novelizer/settings/__init__.py` imports and `__all__`: `write_global_config`, `update_global_config` (from `novelizer.settings.global_store`).

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/settings/test_global_store.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add novelizer/settings tests/settings/test_global_store.py
git commit -m "feat(settings): global_store read-modify-write with 0600 enforcement"
```

---

### Task 3: `setup_core.py` — connectivity probe and config assembly

**Files:**
- Create: `novelizer/settings/setup_core.py`
- Test: `tests/settings/test_setup_core.py`
- Modify: `pyproject.toml` (promote `httpx>=0.28` to a direct dependency — currently transitive)
- Modify: `novelizer/settings/__init__.py` (export `ProbeResult`, `probe_endpoint`, `build_global_config_data`)

**Interfaces:**
- Consumes: nothing new.
- Produces:
  - `ProbeResult` — frozen dataclass: `ok: bool`, `models: list[str]`, `error: str | None`.
  - `async probe_endpoint(base_url: str, api_key: str = "not-needed", timeout: float = 5.0, transport: httpx.AsyncBaseTransport | None = None) -> ProbeResult` — GETs `{base_url.rstrip('/')}/models`, parses OpenAI-style `{"data": [{"id": ...}, ...]}`; never raises (network/HTTP/parse errors land in `.error`). `transport` exists for tests.
  - `build_global_config_data(base_url: str, api_key: str = "", stories_dir: str = "", author_model: str = "", agent_model: str = "", embed_model: str = "") -> dict` — pure; strips whitespace, includes only non-empty values, maps to global-config key names (`llm_base_url`, `llm_api_key`, `default_stories_dir`, `author_model`, `agent_model`, `embed_model`), strips a trailing `/` from the base URL. Raises `ValueError` on empty base_url.

- [ ] **Step 1: Add dependency**

Run: `uv add "httpx>=0.28"`
Expected: `pyproject.toml`/`uv.lock` updated (already installed transitively; this only declares it).

- [ ] **Step 2: Write the failing test**

`tests/settings/test_setup_core.py`:

```python
import httpx
import pytest

from novelizer.settings.setup_core import ProbeResult, build_global_config_data, probe_endpoint


def _transport(handler) -> httpx.AsyncBaseTransport:
    return httpx.MockTransport(handler)


async def test_probe_ok_lists_models():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/v1/models")
        assert request.headers["authorization"] == "Bearer sk-test"
        return httpx.Response(200, json={"data": [{"id": "m-big"}, {"id": "m-fast"}]})

    result = await probe_endpoint("http://h:1/v1", api_key="sk-test", transport=_transport(handler))
    assert result == ProbeResult(ok=True, models=["m-big", "m-fast"], error=None)


async def test_probe_http_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "no"})

    result = await probe_endpoint("http://h:1/v1", transport=_transport(handler))
    assert result.ok is False
    assert result.models == []
    assert "401" in result.error


async def test_probe_connection_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    result = await probe_endpoint("http://h:1/v1", transport=_transport(handler))
    assert result.ok is False
    assert "refused" in result.error


async def test_probe_bad_payload():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not json")

    result = await probe_endpoint("http://h:1/v1", transport=_transport(handler))
    assert result.ok is False
    assert result.error


def test_build_config_data_full():
    data = build_global_config_data(
        base_url=" http://h:1/v1/ ",
        api_key="sk-x",
        stories_dir="~/novels",
        author_model="m1",
        agent_model="m2",
        embed_model="m3",
    )
    assert data == {
        "llm_base_url": "http://h:1/v1",
        "llm_api_key": "sk-x",
        "default_stories_dir": "~/novels",
        "author_model": "m1",
        "agent_model": "m2",
        "embed_model": "m3",
    }


def test_build_config_data_omits_empties():
    data = build_global_config_data(base_url="http://h:1/v1", api_key="  ", author_model="")
    assert data == {"llm_base_url": "http://h:1/v1"}


def test_build_config_data_requires_base_url():
    with pytest.raises(ValueError):
        build_global_config_data(base_url="   ")
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/settings/test_setup_core.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'novelizer.settings.setup_core'`

- [ ] **Step 4: Implement**

`novelizer/settings/setup_core.py`:

```python
from __future__ import annotations

from dataclasses import dataclass, field

import httpx


@dataclass(frozen=True)
class ProbeResult:
    ok: bool
    models: list[str] = field(default_factory=list)
    error: str | None = None


async def probe_endpoint(
    base_url: str,
    api_key: str = "not-needed",
    timeout: float = 5.0,
    transport: httpx.AsyncBaseTransport | None = None,
) -> ProbeResult:
    """Live connectivity test: GET {base_url}/models (OpenAI-compatible).
    Never raises — failures come back as ProbeResult(ok=False, error=...)."""
    url = f"{base_url.rstrip('/')}/models"
    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        async with httpx.AsyncClient(timeout=timeout, transport=transport) as client:
            response = await client.get(url, headers=headers)
        if response.status_code != 200:
            return ProbeResult(ok=False, error=f"HTTP {response.status_code} from {url}")
        payload = response.json()
        models = [m["id"] for m in payload.get("data", []) if isinstance(m, dict) and "id" in m]
        return ProbeResult(ok=True, models=models)
    except Exception as e:  # network, timeout, JSON decode — all become a message
        return ProbeResult(ok=False, error=str(e) or type(e).__name__)


def build_global_config_data(
    base_url: str,
    api_key: str = "",
    stories_dir: str = "",
    author_model: str = "",
    agent_model: str = "",
    embed_model: str = "",
) -> dict:
    """Pure assembly of a global-config dict from wizard fields. Empty fields
    are omitted so built-in defaults keep applying."""
    base = base_url.strip().rstrip("/")
    if not base:
        raise ValueError("LLM base URL is required")
    data: dict = {"llm_base_url": base}
    for key, value in (
        ("llm_api_key", api_key),
        ("default_stories_dir", stories_dir),
        ("author_model", author_model),
        ("agent_model", agent_model),
        ("embed_model", embed_model),
    ):
        value = value.strip()
        if value:
            data[key] = value
    return data
```

Add to `novelizer/settings/__init__.py` imports and `__all__`: `ProbeResult`, `probe_endpoint`, `build_global_config_data` (from `novelizer.settings.setup_core`).

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/settings/test_setup_core.py -v`
Expected: 7 passed

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock novelizer/settings tests/settings/test_setup_core.py
git commit -m "feat(settings): connectivity probe and wizard config assembly (setup_core)"
```

---

### Task 4: `discovery.py` — story listing, ordering, slugs

**Files:**
- Create: `novelizer/settings/discovery.py`
- Test: `tests/settings/test_discovery.py`
- Modify: `novelizer/settings/__init__.py` (export `StoryMeta`, `list_stories`, `order_stories`, `slugify`)

**Interfaces:**
- Consumes: `is_story_dir`, `load_toml_file`, `StoryDirectory` (Phase 1).
- Produces:
  - `StoryMeta` — frozen dataclass: `root: Path`, `title: str`, `mtime: float`.
  - `list_stories(stories_dir: Path) -> list[StoryMeta]` — direct children that are story dirs; title from `story.toml`'s `title` (fallback: directory name; unreadable `story.toml` also falls back); mtime from `world.db` when present else the directory. Missing `stories_dir` → `[]`.
  - `order_stories(stories: list[StoryMeta], last_opened: str | None) -> list[StoryMeta]` — pure: the story whose `str(root)` equals `last_opened` first, remainder by `mtime` descending.
  - `slugify(name: str) -> str` — pure: lowercase, runs of non-alphanumerics → single `-`, trimmed; empty result → `"story"`.

- [ ] **Step 1: Write the failing test**

`tests/settings/test_discovery.py`:

```python
import os

from novelizer.settings.discovery import StoryMeta, list_stories, order_stories, slugify
from novelizer.settings.story_dir import create_story
from novelizer.settings.toml_io import write_toml_file


def test_list_stories_reads_titles_and_skips_non_stories(tmp_path):
    create_story(tmp_path / "alpha", title="Alpha Novel")
    (tmp_path / "beta").mkdir()
    (tmp_path / "beta" / "world.db").write_bytes(b"")  # story dir without story.toml
    (tmp_path / "not-a-story").mkdir()
    (tmp_path / "loose-file.txt").write_text("x")

    stories = {s.root.name: s for s in list_stories(tmp_path)}
    assert set(stories) == {"alpha", "beta"}
    assert stories["alpha"].title == "Alpha Novel"
    assert stories["beta"].title == "beta"


def test_list_stories_missing_dir(tmp_path):
    assert list_stories(tmp_path / "absent") == []


def test_list_stories_bad_story_toml_falls_back_to_dirname(tmp_path):
    sd = create_story(tmp_path / "gamma", title="G")
    sd.story_toml.write_text("title = \n")  # invalid TOML
    [meta] = list_stories(tmp_path)
    assert meta.title == "gamma"


def test_order_stories_last_opened_first_then_mtime(tmp_path):
    a = StoryMeta(root=tmp_path / "a", title="a", mtime=100.0)
    b = StoryMeta(root=tmp_path / "b", title="b", mtime=300.0)
    c = StoryMeta(root=tmp_path / "c", title="c", mtime=200.0)
    ordered = order_stories([a, b, c], last_opened=str(tmp_path / "c"))
    assert [s.root.name for s in ordered] == ["c", "b", "a"]


def test_order_stories_no_last_opened(tmp_path):
    a = StoryMeta(root=tmp_path / "a", title="a", mtime=100.0)
    b = StoryMeta(root=tmp_path / "b", title="b", mtime=300.0)
    assert [s.root.name for s in order_stories([a, b], last_opened=None)] == ["b", "a"]


def test_slugify():
    assert slugify("My Great Novel!") == "my-great-novel"
    assert slugify("  --Weird__ Name--  ") == "weird-name"
    assert slugify("???") == "story"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/settings/test_discovery.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'novelizer.settings.discovery'`

- [ ] **Step 3: Implement**

`novelizer/settings/discovery.py`:

```python
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from novelizer.settings.story_dir import StoryDirectory, is_story_dir
from novelizer.settings.toml_io import TOMLFileError, load_toml_file


@dataclass(frozen=True)
class StoryMeta:
    root: Path
    title: str
    mtime: float


def list_stories(stories_dir: Path) -> list[StoryMeta]:
    if not stories_dir.is_dir():
        return []
    stories: list[StoryMeta] = []
    for child in sorted(stories_dir.iterdir()):
        if not child.is_dir() or not is_story_dir(child):
            continue
        sd = StoryDirectory(root=child)
        title = child.name
        if sd.story_toml.exists():
            try:
                title = load_toml_file(sd.story_toml).get("title") or child.name
            except TOMLFileError:
                pass  # unreadable story.toml: fall back to the directory name
        mtime = sd.db_path.stat().st_mtime if sd.db_path.exists() else child.stat().st_mtime
        stories.append(StoryMeta(root=child, title=title, mtime=mtime))
    return stories


def order_stories(stories: list[StoryMeta], last_opened: str | None) -> list[StoryMeta]:
    """Last-opened story first, remainder most-recently-written first."""
    front = [s for s in stories if last_opened and str(s.root) == last_opened]
    rest = sorted(
        (s for s in stories if s not in front),
        key=lambda s: s.mtime,
        reverse=True,
    )
    return front + rest


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "story"
```

Add to `novelizer/settings/__init__.py` imports and `__all__`: `StoryMeta`, `list_stories`, `order_stories`, `slugify` (from `novelizer.settings.discovery`).

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/settings/test_discovery.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add novelizer/settings tests/settings/test_discovery.py
git commit -m "feat(settings): story discovery, ordering, and slugify (picker core)"
```

---

### Task 5: `SetupWizardApp` — first-run wizard TUI

**Files:**
- Create: `novelizer/tui/setup_wizard.py`
- Test: `tests/tui/test_setup_wizard.py`

**Interfaces:**
- Consumes: `probe_endpoint`, `build_global_config_data`, `ProbeResult` from `novelizer.settings`.
- Produces: `SetupWizardApp(App[dict | None])` — `run()` returns the global-config dict to write, or `None` if the user quit. Constructor: `SetupWizardApp(probe=probe_endpoint)` (probe injectable for tests). Widget ids (relied on by tests): `#base_url`, `#api_key`, `#stories_dir`, `#probe` (button), `#probe_result` (Static), `#author_model`, `#agent_model`, `#embed_model` (Selects), `#save` (button, disabled until a probe succeeds), `#skip` (button — saves URL/key/dir without model choices).

- [ ] **Step 1: Write the failing test**

`tests/tui/test_setup_wizard.py`:

```python
from textual.widgets import Button, Input, Select, Static

from novelizer.settings.setup_core import ProbeResult
from novelizer.tui.setup_wizard import SetupWizardApp


async def _fake_probe_ok(base_url, api_key="not-needed", **kwargs):
    return ProbeResult(ok=True, models=["m-big", "m-fast"])


async def _fake_probe_fail(base_url, api_key="not-needed", **kwargs):
    return ProbeResult(ok=False, error="connection refused")


async def test_probe_then_save_returns_config():
    app = SetupWizardApp(probe=_fake_probe_ok)
    async with app.run_test() as pilot:
        app.query_one("#base_url", Input).value = "http://h:1/v1"
        app.query_one("#api_key", Input).value = "sk-x"
        await pilot.click("#probe")
        await pilot.pause()
        assert "m-big" in str(app.query_one("#probe_result", Static).renderable)
        assert app.query_one("#save", Button).disabled is False
        assert app.query_one("#author_model", Select).value == "m-big"
        await pilot.click("#save")
    assert app.return_value == {
        "llm_base_url": "http://h:1/v1",
        "llm_api_key": "sk-x",
        "default_stories_dir": "stories",
        "author_model": "m-big",
        "agent_model": "m-big",
        "embed_model": "m-big",
    }


async def test_probe_failure_shows_error_and_keeps_save_disabled():
    app = SetupWizardApp(probe=_fake_probe_fail)
    async with app.run_test() as pilot:
        app.query_one("#base_url", Input).value = "http://bad:1/v1"
        await pilot.click("#probe")
        await pilot.pause()
        assert "connection refused" in str(app.query_one("#probe_result", Static).renderable)
        assert app.query_one("#save", Button).disabled is True
        app.exit(None)


async def test_skip_saves_without_models():
    app = SetupWizardApp(probe=_fake_probe_fail)
    async with app.run_test() as pilot:
        app.query_one("#base_url", Input).value = "http://h:1/v1"
        app.query_one("#stories_dir", Input).value = "~/novels"
        await pilot.click("#skip")
    assert app.return_value == {
        "llm_base_url": "http://h:1/v1",
        "default_stories_dir": "~/novels",
    }


async def test_skip_with_blank_base_url_shows_error_not_crash():
    app = SetupWizardApp(probe=_fake_probe_fail)
    async with app.run_test() as pilot:
        app.query_one("#base_url", Input).value = "   "
        await pilot.click("#skip")
        await pilot.pause()
        assert "required" in str(app.query_one("#probe_result", Static).renderable)
        app.exit(None)
    assert app.return_value is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/tui/test_setup_wizard.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'novelizer.tui.setup_wizard'`

- [ ] **Step 3: Implement**

`novelizer/tui/setup_wizard.py`:

```python
from __future__ import annotations

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Footer, Header, Input, Select, Static

from novelizer.settings.setup_core import build_global_config_data, probe_endpoint

_MODEL_SELECT_IDS = ("author_model", "agent_model", "embed_model")


class SetupWizardApp(App[dict | None]):
    """First-run setup: endpoint -> live connectivity test -> model picks.

    run() returns the global-config dict to write, or None if the user quit.
    TUI shell only — probing and config assembly live in settings.setup_core.
    """

    TITLE = "Novelizer — First-run setup"
    BINDINGS = [("q", "quit", "Quit")]

    def __init__(self, probe=probe_endpoint) -> None:
        super().__init__()
        self._probe = probe

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="wizard"):
            yield Static("Point novelizer at your OpenAI-compatible LLM endpoint.")
            yield Input(value="http://localhost:8080/v1", id="base_url", placeholder="LLM base URL")
            yield Input(id="api_key", placeholder="API key (leave blank for local endpoints)", password=True)
            yield Input(value="stories", id="stories_dir", placeholder="Stories directory")
            yield Button("Test connection", id="probe")
            yield Static("", id="probe_result")
            yield Select([], prompt="author model (test connection first)", id="author_model", disabled=True)
            yield Select([], prompt="agent model (test connection first)", id="agent_model", disabled=True)
            yield Select([], prompt="embedding model (test connection first)", id="embed_model", disabled=True)
            with Horizontal(id="wizard_actions"):
                yield Button("Save & continue", id="save", variant="success", disabled=True)
                yield Button("Skip model picks — save endpoint only", id="skip")
        yield Footer()

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "probe":
            await self._run_probe()
        elif event.button.id == "save":
            self._finish(with_models=True)
        elif event.button.id == "skip":
            self._finish(with_models=False)

    async def _run_probe(self) -> None:
        base_url = self.query_one("#base_url", Input).value
        api_key = self.query_one("#api_key", Input).value.strip() or "not-needed"
        result = await self._probe(base_url.strip(), api_key=api_key)
        out = self.query_one("#probe_result", Static)
        if not result.ok:
            out.update(f"✗ {result.error}")
            return
        out.update(f"✓ connected — models: {', '.join(result.models) or '(none reported)'}")
        options = [(m, m) for m in result.models]
        for select_id in _MODEL_SELECT_IDS:
            select = self.query_one(f"#{select_id}", Select)
            select.set_options(options)
            select.disabled = not options
            if options:
                select.value = result.models[0]
        self.query_one("#save", Button).disabled = not options

    def _selected(self, select_id: str) -> str:
        value = self.query_one(f"#{select_id}", Select).value
        return "" if value in (None, Select.BLANK) else str(value)

    def _finish(self, with_models: bool) -> None:
        try:
            data = build_global_config_data(
                base_url=self.query_one("#base_url", Input).value,
                api_key=self.query_one("#api_key", Input).value,
                stories_dir=self.query_one("#stories_dir", Input).value,
                author_model=self._selected("author_model") if with_models else "",
                agent_model=self._selected("agent_model") if with_models else "",
                embed_model=self._selected("embed_model") if with_models else "",
            )
        except ValueError as e:
            self.query_one("#probe_result", Static).update(f"✗ {e}")
            return
        self.exit(data)
```

Note for the implementer: the wizard pre-fills `stories_dir` with `"stories"`, so a user who keeps the default gets `default_stories_dir = "stories"` written explicitly — harmless, it matches the built-in default, and the first test asserts exactly this.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/tui/test_setup_wizard.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add novelizer/tui/setup_wizard.py tests/tui/test_setup_wizard.py
git commit -m "feat(tui): first-run setup wizard with live connectivity test and model picker"
```

---

### Task 6: `StoryPickerApp` — story picker / creation TUI

**Files:**
- Create: `novelizer/tui/story_picker.py`
- Test: `tests/tui/test_story_picker.py`

**Interfaces:**
- Consumes: `StoryMeta`, `order_stories`, `slugify`, `create_story` from `novelizer.settings`.
- Produces: `StoryPickerApp(App[Path | None])` — constructor `StoryPickerApp(stories: list[StoryMeta], stories_dir: Path, last_opened: str | None = None)`; `run()` returns the chosen story root `Path` (existing or newly created), or `None` if the user quit. Widget ids: `#stories` (OptionList; first option is "new story", id `__new__`; story options use `str(root)` as id), `#new_name` (Input, hidden until "new story" chosen), `#picker_error` (Static).

- [ ] **Step 1: Write the failing test**

`tests/tui/test_story_picker.py`:

```python
from pathlib import Path

from textual.widgets import Input, OptionList, Static

from novelizer.settings.discovery import StoryMeta
from novelizer.settings.story_dir import is_story_dir
from novelizer.settings.toml_io import load_toml_file
from novelizer.tui.story_picker import StoryPickerApp


def _metas(tmp_path) -> list[StoryMeta]:
    return [
        StoryMeta(root=tmp_path / "old", title="Old One", mtime=100.0),
        StoryMeta(root=tmp_path / "recent", title="Recent", mtime=200.0),
    ]


async def test_lists_stories_recent_first_and_preselects_last_opened(tmp_path):
    app = StoryPickerApp(_metas(tmp_path), stories_dir=tmp_path, last_opened=str(tmp_path / "old"))
    async with app.run_test() as pilot:
        options = app.query_one("#stories", OptionList)
        # index 0 is "new story"; last-opened comes first among stories
        assert options.get_option_at_index(1).id == str(tmp_path / "old")
        assert options.get_option_at_index(2).id == str(tmp_path / "recent")
        assert options.highlighted == 1
        app.exit(None)


async def test_selecting_story_returns_its_root(tmp_path):
    app = StoryPickerApp(_metas(tmp_path), stories_dir=tmp_path)
    async with app.run_test() as pilot:
        options = app.query_one("#stories", OptionList)
        options.highlighted = 1  # most recent story
        await pilot.press("enter")
    assert app.return_value == tmp_path / "recent"


async def test_new_story_flow_creates_and_returns(tmp_path):
    app = StoryPickerApp([], stories_dir=tmp_path)
    async with app.run_test() as pilot:
        options = app.query_one("#stories", OptionList)
        options.highlighted = 0  # "new story"
        await pilot.press("enter")
        name_input = app.query_one("#new_name", Input)
        assert name_input.display  # revealed
        name_input.value = "My Great Novel!"
        name_input.focus()
        await pilot.pause()
        await pilot.press("enter")
    root = app.return_value
    assert root == tmp_path / "my-great-novel"
    assert is_story_dir(root)
    assert load_toml_file(root / "story.toml") == {"title": "My Great Novel!"}


async def test_new_story_duplicate_slug_shows_error(tmp_path):
    (tmp_path / "taken").mkdir()
    app = StoryPickerApp([], stories_dir=tmp_path)
    async with app.run_test() as pilot:
        options = app.query_one("#stories", OptionList)
        options.highlighted = 0
        await pilot.press("enter")
        name_input = app.query_one("#new_name", Input)
        name_input.value = "Taken"
        name_input.focus()
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert "exists" in str(app.query_one("#picker_error", Static).renderable)
        app.exit(None)
    assert app.return_value is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/tui/test_story_picker.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'novelizer.tui.story_picker'`

- [ ] **Step 3: Implement**

`novelizer/tui/story_picker.py`:

```python
from __future__ import annotations

from pathlib import Path

from textual.app import App, ComposeResult
from textual.widgets import Footer, Header, Input, OptionList, Static
from textual.widgets.option_list import Option

from novelizer.settings.discovery import StoryMeta, order_stories, slugify
from novelizer.settings.story_dir import create_story

_NEW_STORY_ID = "__new__"


class StoryPickerApp(App[Path | None]):
    """Pick an existing story or create a new one.

    run() returns the chosen story root, or None if the user quit.
    Ordering/slug logic lives in settings.discovery; this is the TUI shell.
    """

    TITLE = "Novelizer — Choose a story"
    BINDINGS = [("q", "quit", "Quit")]

    def __init__(
        self,
        stories: list[StoryMeta],
        stories_dir: Path,
        last_opened: str | None = None,
    ) -> None:
        super().__init__()
        self._stories = order_stories(stories, last_opened)
        self._stories_dir = stories_dir

    def compose(self) -> ComposeResult:
        yield Header()
        options = [Option("➕  New story", id=_NEW_STORY_ID)]
        options += [Option(f"{s.title}  ({s.root})", id=str(s.root)) for s in self._stories]
        option_list = OptionList(*options, id="stories")
        yield option_list
        yield Input(id="new_name", placeholder="New story name…")
        yield Static("", id="picker_error")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#new_name", Input).display = False
        option_list = self.query_one("#stories", OptionList)
        # Preselect the last-opened story (index 1) when present, else "new story".
        option_list.highlighted = 1 if self._stories else 0
        option_list.focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option.id == _NEW_STORY_ID:
            name_input = self.query_one("#new_name", Input)
            name_input.display = True
            name_input.focus()
        else:
            self.exit(Path(event.option.id))

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "new_name":
            return
        name = event.value.strip()
        if not name:
            self.query_one("#picker_error", Static).update("✗ name required")
            return
        root = self._stories_dir / slugify(name)
        if root.exists():
            self.query_one("#picker_error", Static).update(f"✗ {root} already exists")
            return
        create_story(root, title=name)
        self.exit(root)
```

Note for the implementer: pilot key routing depends on focus — the `focus()` + `pause()` before `press("enter")` in the tests is required, not decorative.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/tui/test_story_picker.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add novelizer/tui/story_picker.py tests/tui/test_story_picker.py
git commit -m "feat(tui): story picker with recent-first ordering and new-story creation"
```

---

### Task 7: CLI orchestration — wizard/picker boot, resolution precedence, persistence

**Files:**
- Modify: `novelizer/director/cli.py` (imports, `_resolve_story`, `cli` group, new `_interactive_startup`)
- Modify: `tests/director/test_resolve_story.py` (signature + new behavior)
- Test: `tests/director/test_startup.py` (new)

**Interfaces:**
- Consumes: everything from Tasks 1–6 plus Phase 1 exports.
- Produces (in `novelizer/director/cli.py`):
  - `_resolve_story(story_path: str | None, stories_root: Path, base, confirm=click.confirm, global_path: Path | None = None) -> StoryDirectory` — `base` is a story-less `EffectiveSettings`. Precedence: explicit (validated) → valid `last_opened_story` → flat-migration flow (prompt unless `base.suppress_flat_migration_prompt`; decline persists the flag via `update_global_config` and keeps legacy paths) → reuse-or-create `stories_root/default`.
  - `_validated_story(story_path: str) -> StoryDirectory` — raises `click.ClickException` if the path is not an existing story dir.
  - `_interactive_startup(story_path: str | None, run_wizard=None, run_picker=None) -> EffectiveSettings | None` — wizard if global config missing (quit → `None`); then explicit story or picker (quit → `None`); records `last_opened_story`; returns loaded settings. `run_wizard()`/`run_picker(stories, stories_dir, last_opened)` injectable for tests; defaults run the real Textual apps.
  - `cli` group: TUI path uses `_interactive_startup`; subcommand path uses story-less load → `_resolve_story` → full load, and also records `last_opened_story`.

- [ ] **Step 1: Update the failing resolution tests**

Replace the body of `tests/director/test_resolve_story.py` with (same filename):

```python
import pytest
from click import ClickException

from novelizer.director.cli import _resolve_story, _validated_story
from novelizer.settings import EffectiveSettings, create_story
from novelizer.settings.toml_io import load_toml_file


def _base(**kwargs) -> EffectiveSettings:
    return EffectiveSettings(**kwargs)


def test_explicit_story_path_must_exist(tmp_path):
    with pytest.raises(ClickException):
        _validated_story(str(tmp_path / "nope"))


def test_explicit_story_path_valid(tmp_path):
    create_story(tmp_path / "novel", title="N")
    sd = _validated_story(str(tmp_path / "novel"))
    assert sd.root == tmp_path / "novel"


def test_last_opened_story_used_when_valid(tmp_path):
    create_story(tmp_path / "recent", title="R")
    sd = _resolve_story(
        None,
        stories_root=tmp_path / "stories",
        base=_base(last_opened_story=str(tmp_path / "recent")),
        global_path=tmp_path / "config.toml",
    )
    assert sd.root == tmp_path / "recent"


def test_stale_last_opened_falls_through(tmp_path):
    root = tmp_path / "stories"
    sd = _resolve_story(
        None,
        stories_root=root,
        base=_base(last_opened_story=str(tmp_path / "deleted")),
        global_path=tmp_path / "config.toml",
    )
    assert sd.root == root / "default"


def test_flat_layout_migrates_when_confirmed(tmp_path):
    root = tmp_path / "stories"
    root.mkdir()
    (root / "world.db").write_bytes(b"db")
    sd = _resolve_story(
        None, stories_root=root, base=_base(),
        confirm=lambda *a, **k: True, global_path=tmp_path / "config.toml",
    )
    assert sd.root == root / "default"
    assert sd.db_path.read_bytes() == b"db"


def test_flat_layout_decline_persists_suppression(tmp_path):
    root = tmp_path / "stories"
    root.mkdir()
    (root / "world.db").write_bytes(b"db")
    gpath = tmp_path / "config.toml"
    sd = _resolve_story(
        None, stories_root=root, base=_base(),
        confirm=lambda *a, **k: False, global_path=gpath,
    )
    assert sd.root == root  # legacy paths keep working
    assert load_toml_file(gpath)["suppress_flat_migration_prompt"] is True


def test_flat_layout_suppressed_never_prompts(tmp_path):
    root = tmp_path / "stories"
    root.mkdir()
    (root / "world.db").write_bytes(b"db")

    def _boom(*a, **k):
        raise AssertionError("must not prompt when suppressed")

    sd = _resolve_story(
        None, stories_root=root,
        base=_base(suppress_flat_migration_prompt=True),
        confirm=_boom, global_path=tmp_path / "config.toml",
    )
    assert sd.root == root


def test_fresh_install_creates_default_story(tmp_path):
    root = tmp_path / "stories"
    sd = _resolve_story(
        None, stories_root=root, base=_base(), global_path=tmp_path / "config.toml"
    )
    assert sd.root == root / "default"
    assert sd.story_toml.exists()
```

- [ ] **Step 2: Write the failing startup tests**

`tests/director/test_startup.py`:

```python
from pathlib import Path

from novelizer.director.cli import _interactive_startup
from novelizer.settings import create_story
from novelizer.settings.toml_io import load_toml_file, write_toml_file


def _isolate(monkeypatch, tmp_path) -> Path:
    """Point the global config and env at a clean sandbox."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.chdir(tmp_path)
    from novelizer.settings.loader import EnvOverrides

    for name in EnvOverrides.model_fields:
        monkeypatch.delenv(f"NOVELIZER_{name.upper()}", raising=False)
    return tmp_path / "xdg" / "novelizer" / "config.toml"


def test_wizard_runs_when_config_missing_and_quit_aborts(monkeypatch, tmp_path):
    gpath = _isolate(monkeypatch, tmp_path)
    calls = []

    def fake_wizard():
        calls.append("wizard")
        return None  # user quit

    result = _interactive_startup(None, run_wizard=fake_wizard, run_picker=lambda *a: None)
    assert result is None
    assert calls == ["wizard"]
    assert not gpath.exists()


def test_wizard_result_written_0600_then_picker(monkeypatch, tmp_path):
    import os

    gpath = _isolate(monkeypatch, tmp_path)
    story = create_story(tmp_path / "stories" / "novel", title="N")

    def fake_wizard():
        return {"llm_base_url": "http://h:1/v1", "llm_api_key": "sk-x"}

    def fake_picker(stories, stories_dir, last_opened):
        assert stories_dir == Path("stories")
        return story.root

    settings = _interactive_startup(None, run_wizard=fake_wizard, run_picker=fake_picker)
    assert settings is not None
    assert settings.llm_base_url == "http://h:1/v1"
    assert settings.db_path == str(story.db_path)
    assert os.stat(gpath).st_mode & 0o777 == 0o600
    assert load_toml_file(gpath)["last_opened_story"] == str(story.root)


def test_existing_config_skips_wizard_and_honors_default_stories_dir(monkeypatch, tmp_path):
    gpath = _isolate(monkeypatch, tmp_path)
    write_toml_file(gpath, {"default_stories_dir": str(tmp_path / "novels")})
    story = create_story(tmp_path / "novels" / "one", title="One")
    seen = {}

    def fake_picker(stories, stories_dir, last_opened):
        seen["stories_dir"] = stories_dir
        seen["titles"] = [s.title for s in stories]
        return story.root

    settings = _interactive_startup(
        None,
        run_wizard=lambda: (_ for _ in ()).throw(AssertionError("wizard must not run")),
        run_picker=fake_picker,
    )
    assert seen["stories_dir"] == tmp_path / "novels"
    assert seen["titles"] == ["One"]
    assert settings.db_path == str(story.db_path)


def test_picker_quit_aborts_without_recording(monkeypatch, tmp_path):
    gpath = _isolate(monkeypatch, tmp_path)
    write_toml_file(gpath, {})
    result = _interactive_startup(None, run_wizard=lambda: {}, run_picker=lambda *a: None)
    assert result is None
    assert "last_opened_story" not in load_toml_file(gpath)


def test_explicit_story_bypasses_picker(monkeypatch, tmp_path):
    gpath = _isolate(monkeypatch, tmp_path)
    write_toml_file(gpath, {})
    story = create_story(tmp_path / "elsewhere" / "novel", title="N")

    settings = _interactive_startup(
        str(story.root),
        run_wizard=lambda: (_ for _ in ()).throw(AssertionError("no wizard")),
        run_picker=lambda *a: (_ for _ in ()).throw(AssertionError("no picker")),
    )
    assert settings.db_path == str(story.db_path)
    assert load_toml_file(gpath)["last_opened_story"] == str(story.root)
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/director -v`
Expected: FAIL — `_validated_story` / `_interactive_startup` don't exist; `_resolve_story` has the old signature.

- [ ] **Step 4: Implement**

In `novelizer/director/cli.py`:

Extend the settings import to include the new names:

```python
from novelizer.settings import (
    EffectiveSettings,
    StoryDirectory,
    StoryConfigError,
    TOMLFileError,
    create_story,
    global_config_path,
    is_story_dir,
    list_stories,
    load_effective_settings,
    migrate_flat_layout,
    update_global_config,
    write_global_config,
)
```

Replace `_resolve_story` and add the new helpers:

```python
def _validated_story(story_path: str) -> StoryDirectory:
    root = Path(story_path).expanduser()
    if not is_story_dir(root):
        raise click.ClickException(
            f"{root} is not a story directory (no story.toml or world.db). "
            "Check the path, or run `novelizer` without --story to pick or create one."
        )
    return StoryDirectory(root=root)


def _resolve_story(
    story_path: str | None,
    stories_root: Path,
    base: EffectiveSettings,
    confirm=click.confirm,
    global_path: Path | None = None,
) -> StoryDirectory:
    """Headless story resolution: --story -> last-opened -> legacy migration -> default."""
    if story_path:
        return _validated_story(story_path)
    if base.last_opened_story and is_story_dir(Path(base.last_opened_story)):
        return StoryDirectory(root=Path(base.last_opened_story))
    if (stories_root / "world.db").exists():
        if base.suppress_flat_migration_prompt:
            return StoryDirectory(root=stories_root)
        if confirm(
            f"Found legacy flat story at {stories_root}/world.db. "
            f"Migrate it into {stories_root}/default/?",
            default=True,
        ):
            return migrate_flat_layout(stories_root)
        update_global_config(path=global_path, suppress_flat_migration_prompt=True)
        return StoryDirectory(root=stories_root)  # legacy paths keep working
    default = stories_root / "default"
    if is_story_dir(default):
        return StoryDirectory(root=default)
    return create_story(default, title="default")


def _run_wizard_app() -> dict | None:
    from novelizer.tui.setup_wizard import SetupWizardApp

    return SetupWizardApp().run()


def _run_picker_app(stories, stories_dir: Path, last_opened: str | None):
    from novelizer.tui.story_picker import StoryPickerApp

    return StoryPickerApp(stories, stories_dir=stories_dir, last_opened=last_opened).run()


def _interactive_startup(
    story_path: str | None,
    run_wizard=None,
    run_picker=None,
) -> EffectiveSettings | None:
    """TUI boot: wizard when unconfigured, then story pick. None = user quit."""
    run_wizard = run_wizard or _run_wizard_app
    run_picker = run_picker or _run_picker_app
    if not global_config_path().exists():
        wizard_data = run_wizard()
        if wizard_data is None:
            return None
        write_global_config(wizard_data)
    base = load_effective_settings()
    stories_root = Path(base.default_stories_dir).expanduser()
    if story_path:
        story = _validated_story(story_path)
    else:
        if (stories_root / "world.db").exists() and not base.suppress_flat_migration_prompt:
            if click.confirm(
                f"Found legacy flat story at {stories_root}/world.db. "
                f"Migrate it into {stories_root}/default/?",
                default=True,
            ):
                migrate_flat_layout(stories_root)
            else:
                update_global_config(suppress_flat_migration_prompt=True)
            base = load_effective_settings()
        chosen = run_picker(list_stories(stories_root), stories_root, base.last_opened_story)
        if chosen is None:
            return None
        story = StoryDirectory(root=Path(chosen))
    update_global_config(last_opened_story=str(story.root))
    return load_effective_settings(story_dir=story)
```

Replace the `cli` group body (keep the `--story` option and the existing `except` wrapper style from Phase 1 — `TOMLFileError`, `StoryConfigError`, `FileExistsError` → `click.ClickException`):

```python
@click.group(invoke_without_command=True)
@click.option("--story", "story_path", default=None, type=click.Path(), help="Path to a story directory.")
@click.pass_context
def cli(ctx, story_path: str | None):
    ctx.ensure_object(dict)
    try:
        if ctx.invoked_subcommand is None:
            settings = _interactive_startup(story_path)
            if settings is None:
                return  # user quit the wizard or picker
            _launch_tui(settings)
            return
        base = load_effective_settings()
        stories_root = Path(base.default_stories_dir).expanduser()
        story = _resolve_story(story_path, stories_root, base)
        update_global_config(last_opened_story=str(story.root))
        ctx.obj["settings"] = load_effective_settings(story_dir=story)
    except (TOMLFileError, StoryConfigError, FileExistsError) as e:
        raise click.ClickException(str(e)) from e
```

Also: `from novelizer.settings.discovery import list_stories` is already covered by the package import above. Add `import novelizer.settings.discovery` only if the package `__init__` export from Task 4 was missed.

**Required companion edit — isolate the existing CLI test from the real global config.** The `cli` group now calls `load_effective_settings()` (reads the developer's real `~/.config/novelizer/config.toml`) and `update_global_config(last_opened_story=...)` (would WRITE to it) on the subcommand path. `tests/director/test_cli.py`'s existing friendly-error test must therefore sandbox the global config. Add at the top of that test (keeping everything else):

```python
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
```

(add `monkeypatch` / `tmp_path` to its signature if absent). Any other test that invokes the `cli` group directly needs the same isolation — grep `tests/` for `CliRunner` and treat each hit.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/director -v`
Expected: all pass (resolution + startup + Phase 1's friendly-error test, which needs no change).

- [ ] **Step 6: Run the full test suite**

Run: `uv run pytest -q`
Expected: all pass (known pre-existing flake exempted: `tests/canon/test_event_store.py::test_sequences_are_strictly_increasing` Hypothesis DeadlineExceeded).

- [ ] **Step 7: Commit**

```bash
git add novelizer/director/cli.py tests/director
git commit -m "feat(cli): wizard/picker boot flow, story resolution precedence, migration-decline persistence"
```

---

### Task 8: Docs — README first-run section and example-config updates

**Files:**
- Modify: `README.md` (Configuration section: first-run + picker behavior)
- Modify: `docs/examples/config.example.toml` (document `suppress_flat_migration_prompt` and `last_opened_story` as app-managed; refresh the 0600 note now that the wizard enforces it)

**Interfaces:** documentation only.

- [ ] **Step 1: Update the example config**

In `docs/examples/config.example.toml`, in the `# --- App ---` section, replace the existing `default_stories_dir` caveat comment (it is now consumed) and append the app-managed keys as comments:

```toml
# --- App ---
default_stories_dir = "stories"

# Managed by novelizer itself — no need to hand-edit:
# last_opened_story = "/path/to/last/story"        # picker preselection
# suppress_flat_migration_prompt = true            # set when you decline legacy migration
```

Also update the API-key comment from "the setup wizard will write it that way" phrasing to reflect reality: the wizard now writes this file with mode 0600.

- [ ] **Step 2: Update README**

In the README's Configuration section, replace the sentence describing bare-`novelizer` behavior with:

```markdown
On first launch (no global config yet), novelizer opens a setup wizard: point
it at your OpenAI-compatible endpoint, test the connection, and pick models
from the endpoint's live model list. After setup — and on every later
launch — a story picker lists the stories in `default_stories_dir`
(most recent first, last-opened preselected) and can create new ones.
`novelizer --story path/to/story/` skips the picker. A legacy flat
`stories/world.db` triggers a one-time migration offer; declining is
remembered.
```

- [ ] **Step 3: Verify nothing broke**

Run: `uv run pytest -q`
Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add README.md docs/examples/config.example.toml
git commit -m "docs: first-run wizard and story picker documentation"
```

---

## Post-plan notes

- The stale root `.env.example` in the **main checkout** is untracked and unreachable from this worktree; delete it manually there (carried over from Phase 1).
- Phase 3 (TUI settings screen, live-apply, file watching, provenance stamping) gets its own plan after this lands.
- Deferred by design: `--story` on a nonexistent path does not offer creation (picker owns creation); wizard model Selects require a successful probe (the Skip button covers offline first-runs); `voice_pack`/`prose_profile` selection in the new-story flow (Phase 3's settings screen owns voice UX).
