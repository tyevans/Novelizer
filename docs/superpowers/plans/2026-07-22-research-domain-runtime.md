# Research Domain Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `research_domain` actually runnable — a minimal event-store-backed runtime and CLI to append events and inspect projections — built on a new, storage-agnostic `substrate.RuntimeBase` primitive generalized from the connect/replay/close pattern (not the code) of novelizer's runtime.

**Architecture:** `substrate/runtime.py`'s `RuntimeBase` wires an event store to a set of registered `(ProjectionCatalog, projection_name, triggering event types)` triples and knows how to replay a stream into them. `research_domain/runtime.py`'s `ResearchRuntime` subclasses it with the three existing projection catalogs and domain-specific lookup-dict maintenance. `research_domain/cli.py` is a thin `click` CLI (`append`, `show`) on top of `ResearchRuntime`.

**Tech Stack:** Python 3.13, `uv`, `pytest` + `pytest-asyncio` (`asyncio_mode = "auto"`), `asyncpg` via `substrate.PostgresEventStore`, `click`, `rich`.

## Global Constraints

- No LLM-backed agents — `research_domain/roles.py`'s six stub `AgentSpec`s are untouched. The CLI's `append` command is the only way events enter the stream.
- No gating/autonomy enforcement in the CLI — `append` writes directly, unmediated by `is_gated`/tier checks.
- No new Postgres schema beyond what `PostgresEventStore` already creates.
- No changes to `novelizer/` code, `novelizer`'s `Runtime`, or `novelizer`'s CLI. `RuntimeBase` is a new substrate primitive, not a novelizer migration.
- No incremental/partial invalidation optimization — every `catch_up()` does a full replay-and-recompute over the whole stream. Acceptable at this scale (CLI-driven proof-of-concept).
- `substrate/runtime.py`'s public names (`RuntimeBase`) must be added to `substrate/__init__.py`'s `__all__` and to the `forbidden_modules` list in `pyproject.toml`'s `[[tool.importlinter.contracts]]` block (`substrate.runtime`), keeping the existing package-boundary enforcement from the prior session consistent with this new submodule.
- Use `uv run pytest`, not bare `pytest`. Run tests targeted to the files touched, never the full suite (finite compute resources — standing instruction).
- Postgres-backed tests use the existing `tests/substrate/postgres_fixture.py`'s `postgres_dsn` fixture (Docker-backed; skips if Docker unavailable) — do not write a new fixture.

---

### Task 1: `substrate.RuntimeBase` — generic replay-and-catch-up primitive

**Files:**
- Create: `substrate/runtime.py`
- Modify: `substrate/__init__.py` (add `RuntimeBase` to imports and `__all__`)
- Modify: `pyproject.toml` (add `"substrate.runtime"` to the import-linter contract's `forbidden_modules`)
- Test: `tests/substrate/test_runtime.py`

**Interfaces:**
- Consumes: `substrate.PostgresEventStore` (`connect`, `close`, `append(stream_id, event_type, payload, ...)`, `read_stream(stream_id) -> list[dict]` where each dict has `"event_type"` and `"payload"` keys — see `substrate/postgres/events.py`), `substrate.ProjectionCatalog` (`register`, `invalidate(projection_name, source_event)`, `recompute_dirty(projection_name) -> dict` — `async def`), `substrate.ProjectionSpec`.
- Produces: `RuntimeBase.__init__(event_store, stream: str)`; `register_projection(catalog, projection_name: str, event_types: set[str]) -> None`; `async def connect() -> None`; `async def append(event_type: str, payload: dict, **kwargs) -> int` (delegates to the event store, returns the sequence number); `async def catch_up() -> None`; `def get_projection(projection_name: str) -> dict` (returns `{}` if never computed); `async def close() -> None`. Task 2 subclasses this and relies on `self._event_store` and `self._stream` being available as protected attributes.

- [ ] **Step 1: Write the failing test**

Create `tests/substrate/test_runtime.py`:

```python
# tests/substrate/test_runtime.py
import pytest

from substrate import PostgresEventStore, ProjectionCatalog, ProjectionSpec
from substrate.runtime import RuntimeBase
from tests.substrate.postgres_fixture import postgres_dsn


@pytest.mark.asyncio
async def test_catch_up_dispatches_only_to_catalogs_registered_for_the_event_type(postgres_dsn):
    store = PostgresEventStore(postgres_dsn)
    runtime = RuntimeBase(store, "runtime-test-stream")
    await runtime.connect()
    try:
        seen_a: dict[str, int] = {}
        catalog_a = ProjectionCatalog()
        catalog_a.register(
            ProjectionSpec(
                name="a",
                invalidation_key=lambda event: event.payload["id"],
                recompute=lambda key: seen_a.get(key, 0) + 1,
            )
        )
        seen_b: dict[str, int] = {}
        catalog_b = ProjectionCatalog()
        catalog_b.register(
            ProjectionSpec(
                name="b",
                invalidation_key=lambda event: event.payload["id"],
                recompute=lambda key: seen_b.get(key, 0) + 1,
            )
        )

        runtime.register_projection(catalog_a, "a", {"type.a"})
        runtime.register_projection(catalog_b, "b", {"type.b"})

        await runtime.append("type.a", {"id": "x"})
        await runtime.append("type.b", {"id": "y"})

        await runtime.catch_up()

        assert runtime.get_projection("a") == {"x": 1}
        assert runtime.get_projection("b") == {"y": 1}
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_get_projection_returns_empty_dict_before_any_catch_up(postgres_dsn):
    store = PostgresEventStore(postgres_dsn)
    runtime = RuntimeBase(store, "runtime-test-stream-2")
    await runtime.connect()
    try:
        assert runtime.get_projection("nonexistent") == {}
    finally:
        await runtime.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/substrate/test_runtime.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'substrate.runtime'`

- [ ] **Step 3: Write `substrate/runtime.py`**

```python
# substrate/runtime.py
from __future__ import annotations
from dataclasses import dataclass

from substrate.postgres.events import PostgresEventStore
from substrate.projection import ProjectionCatalog


class _EventView:
    """Wraps a read_stream() row's payload so it satisfies the `.payload`
    attribute access every ProjectionSpec.invalidation_key lambda expects."""

    def __init__(self, payload: dict) -> None:
        self.payload = payload


@dataclass(frozen=True)
class _ProjectionRegistration:
    catalog: ProjectionCatalog
    projection_name: str
    event_types: frozenset[str]


class RuntimeBase:
    """Storage-agnostic lifecycle: connect an event store, replay its stream
    into registered projections, expose read access, close cleanly.

    Domain runtimes subclass this and register their own ProjectionCatalogs
    against the event types that should invalidate them.
    """

    def __init__(self, event_store: PostgresEventStore, stream: str) -> None:
        self._event_store = event_store
        self._stream = stream
        self._registrations: list[_ProjectionRegistration] = []
        self._results: dict[str, dict] = {}

    def register_projection(
        self, catalog: ProjectionCatalog, projection_name: str, event_types: set[str]
    ) -> None:
        self._registrations.append(
            _ProjectionRegistration(catalog, projection_name, frozenset(event_types))
        )

    async def connect(self) -> None:
        await self._event_store.connect()

    async def append(self, event_type: str, payload: dict, **kwargs) -> int:
        return await self._event_store.append(self._stream, event_type, payload, **kwargs)

    async def catch_up(self) -> None:
        events = await self._event_store.read_stream(self._stream)
        for registration in self._registrations:
            for event in events:
                if event["event_type"] in registration.event_types:
                    registration.catalog.invalidate(
                        registration.projection_name, _EventView(event["payload"])
                    )
            self._results[registration.projection_name] = await registration.catalog.recompute_dirty(
                registration.projection_name
            )

    def get_projection(self, projection_name: str) -> dict:
        return self._results.get(projection_name, {})

    async def close(self) -> None:
        await self._event_store.close()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/substrate/test_runtime.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Add `RuntimeBase` to the substrate public API**

In `substrate/__init__.py`, add the import and `__all__` entry. The full file becomes:

```python
from __future__ import annotations

from substrate.agent_registry import AgentContext, AgentSpec, SubagentGrant, ToolGrant
from substrate.event_registry import EventTypeRegistry, EventTypeSpec, GatingTier
from substrate.policy import is_gated
from substrate.postgres.deps import PostgresDepsStore
from substrate.postgres.embeddings import PostgresEmbeddingStore
from substrate.postgres.events import PostgresEventStore
from substrate.projection import ProjectionCatalog, ProjectionSpec
from substrate.runtime import RuntimeBase

__all__ = [
    "AgentContext",
    "AgentSpec",
    "EventTypeRegistry",
    "EventTypeSpec",
    "GatingTier",
    "PostgresDepsStore",
    "PostgresEmbeddingStore",
    "PostgresEventStore",
    "ProjectionCatalog",
    "ProjectionSpec",
    "RuntimeBase",
    "SubagentGrant",
    "ToolGrant",
    "is_gated",
]
```

- [ ] **Step 6: Keep the import-linter contract consistent**

In `pyproject.toml`, the `[[tool.importlinter.contracts]]` block's `forbidden_modules` list currently reads:

```toml
forbidden_modules = [
    "substrate.agent_registry",
    "substrate.event_registry",
    "substrate.policy",
    "substrate.postgres",
    "substrate.projection",
]
```

Change it to add `"substrate.runtime"`, keeping alphabetical order:

```toml
forbidden_modules = [
    "substrate.agent_registry",
    "substrate.event_registry",
    "substrate.policy",
    "substrate.postgres",
    "substrate.projection",
    "substrate.runtime",
]
```

- [ ] **Step 7: Verify the import-linter contract and public API test still pass**

Run: `uv run lint-imports`
Expected: `Contracts: 1 kept, 0 broken.`

Run: `uv run pytest tests/substrate/test_public_api.py -v`
Expected: FAIL — `test_all_matches_expected_public_api` will fail because `substrate.__all__` now has 14 names, not the 13 the test currently pins. Update `tests/substrate/test_public_api.py`'s `EXPECTED_PUBLIC_API` list to add `"RuntimeBase"` in alphabetical position (between `"ProjectionSpec"` and `"SubagentGrant"`):

```python
EXPECTED_PUBLIC_API = [
    "AgentContext",
    "AgentSpec",
    "EventTypeRegistry",
    "EventTypeSpec",
    "GatingTier",
    "PostgresDepsStore",
    "PostgresEmbeddingStore",
    "PostgresEventStore",
    "ProjectionCatalog",
    "ProjectionSpec",
    "RuntimeBase",
    "SubagentGrant",
    "ToolGrant",
    "is_gated",
]
```

Run: `uv run pytest tests/substrate/test_public_api.py tests/substrate/test_runtime.py -v`
Expected: PASS (4 passed)

- [ ] **Step 8: Run the full `tests/substrate/` suite as a regression check**

Run: `uv run pytest tests/substrate/ -v`
Expected: all pass.

- [ ] **Step 9: Commit**

```bash
git add substrate/runtime.py substrate/__init__.py pyproject.toml tests/substrate/test_runtime.py tests/substrate/test_public_api.py
git commit -m "feat(substrate): add RuntimeBase, a generic event-replay-to-projections primitive"
```

---

### Task 2: `research_domain.ResearchRuntime`

**Files:**
- Create: `research_domain/runtime.py`
- Test: `tests/research_domain/test_runtime.py`

**Interfaces:**
- Consumes: `substrate.RuntimeBase` (Task 1) — `__init__(event_store, stream)`, `register_projection`, `connect`, `append`, `catch_up`, `get_projection`, `close`, and the protected `self._event_store`/`self._stream` attributes it sets. `substrate.PostgresEventStore`. `research_domain.projections.build_source_coverage_catalog`, `build_contradiction_map_catalog`, `build_claim_dependency_catalog` (all pre-existing, from `research_domain/projections.py` — see that file's exact signatures, unchanged by this task).
- Produces: `ResearchRuntime(dsn: str, stream: str = "research-stream")`; `async def append_event(event_type: str, payload: dict) -> None` (appends then re-catches-up so projections reflect it immediately). Task 3's CLI relies on exactly this constructor signature and `append_event` method name, plus the inherited `connect`, `catch_up`, `get_projection`, `close` from `RuntimeBase`.

- [ ] **Step 1: Write the failing test**

Create `tests/research_domain/test_runtime.py`:

```python
# tests/research_domain/test_runtime.py
import pytest

from research_domain.runtime import ResearchRuntime
from tests.substrate.postgres_fixture import postgres_dsn


@pytest.mark.asyncio
async def test_research_runtime_keeps_all_three_projections_current_across_appends(postgres_dsn):
    runtime = ResearchRuntime(postgres_dsn, stream="research-runtime-test-stream")
    await runtime.connect()
    try:
        await runtime.append_event(
            "claim.proposed", {"claim_id": "claim-1", "source_id": "source-a", "text": "the sky is green"}
        )
        await runtime.append_event(
            "claim.proposed", {"claim_id": "claim-2", "source_id": "source-b", "text": "the sky is blue"}
        )
        assert runtime.get_projection("source_coverage") == {"source-a": 1, "source-b": 1}

        await runtime.append_event(
            "claim.refuted",
            {"claim_id": "claim-2", "target_claim_id": "claim-1", "reason": "source-b directly observed"},
        )
        assert runtime.get_projection("contradiction_map") == {"claim-1": ["claim-2"]}

        await runtime.append_event(
            "claim.proposed", {"claim_id": "claim-3", "source_id": "source-c", "text": "the sky is blue at noon"}
        )
        await runtime.append_event(
            "claim.corrected",
            {"claim_id": "claim-3", "target_claim_id": "claim-2", "reason": "time-of-day qualifier added"},
        )
        assert runtime.get_projection("claim_dependency_graph") == {"claim-2": ["claim-3"]}
        assert runtime.get_projection("source_coverage") == {"source-a": 1, "source-b": 1, "source-c": 1}
    finally:
        await runtime.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/research_domain/test_runtime.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'research_domain.runtime'`

- [ ] **Step 3: Write `research_domain/runtime.py`**

```python
# research_domain/runtime.py
from __future__ import annotations

from substrate import PostgresEventStore, RuntimeBase
from research_domain.projections import (
    build_claim_dependency_catalog,
    build_contradiction_map_catalog,
    build_source_coverage_catalog,
)


class ResearchRuntime(RuntimeBase):
    """Wires the three research_domain projections to a Postgres-backed
    RuntimeBase. Lookup dicts are refreshed from the full event stream on
    every catch_up() so the catalogs' recompute closures always see current
    data -- see the class-level note on _refresh_lookup_dicts."""

    def __init__(self, dsn: str, stream: str = "research-stream") -> None:
        super().__init__(PostgresEventStore(dsn), stream)

        # These dicts are mutated in place (never rebound) by
        # _refresh_lookup_dicts, so the closures below -- captured once here
        # and handed to the projection catalogs -- always see current data.
        self._counts_by_source: dict[str, int] = {}
        self._refuters_by_target: dict[str, list[str]] = {}
        self._superseders_by_target: dict[str, list[str]] = {}

        source_coverage = build_source_coverage_catalog(
            lambda source_id: self._counts_by_source[source_id]
        )
        contradiction_map = build_contradiction_map_catalog(
            lambda target_claim_id: self._refuters_by_target[target_claim_id]
        )
        claim_dependency_graph = build_claim_dependency_catalog(
            lambda target_claim_id: self._superseders_by_target[target_claim_id]
        )

        self.register_projection(source_coverage, "source_coverage", {"claim.proposed"})
        self.register_projection(contradiction_map, "contradiction_map", {"claim.refuted"})
        self.register_projection(
            claim_dependency_graph, "claim_dependency_graph", {"claim.corrected"}
        )

    async def _refresh_lookup_dicts(self) -> None:
        events = await self._event_store.read_stream(self._stream)
        self._counts_by_source.clear()
        self._refuters_by_target.clear()
        self._superseders_by_target.clear()
        for event in events:
            if event["event_type"] == "claim.proposed":
                source_id = event["payload"]["source_id"]
                self._counts_by_source[source_id] = self._counts_by_source.get(source_id, 0) + 1
            elif event["event_type"] == "claim.refuted":
                target_id = event["payload"]["target_claim_id"]
                self._refuters_by_target.setdefault(target_id, []).append(event["payload"]["claim_id"])
            elif event["event_type"] == "claim.corrected":
                target_id = event["payload"]["target_claim_id"]
                self._superseders_by_target.setdefault(target_id, []).append(event["payload"]["claim_id"])

    async def catch_up(self) -> None:
        await self._refresh_lookup_dicts()
        await super().catch_up()

    async def append_event(self, event_type: str, payload: dict) -> None:
        await self.append(event_type, payload)
        await self.catch_up()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/research_domain/test_runtime.py -v`
Expected: PASS (1 passed)

- [ ] **Step 5: Run the full `tests/research_domain/` suite as a regression check**

Run: `uv run pytest tests/research_domain/ -v`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add research_domain/runtime.py tests/research_domain/test_runtime.py
git commit -m "feat(research-domain): add ResearchRuntime wiring the three projections to RuntimeBase"
```

---

### Task 3: `research_domain` CLI

**Files:**
- Create: `research_domain/cli.py`
- Modify: `pyproject.toml` (add `[project.scripts]` entry)
- Test: `tests/research_domain/test_cli.py`

**Interfaces:**
- Consumes: `research_domain.runtime.ResearchRuntime` (Task 2) — exact constructor `ResearchRuntime(dsn, stream="research-stream")`, `connect()`, `append_event(event_type, payload)`, `catch_up()`, `get_projection(name) -> dict`, `close()`.
- Produces: a `click` group `main` in `research_domain/cli.py`, with subcommands `append EVENT_TYPE PAYLOAD_JSON [--dsn DSN]` and `show PROJECTION_NAME [--dsn DSN]`. Registered as the `research-domain` console script.

- [ ] **Step 1: Write the failing test**

Create `tests/research_domain/test_cli.py`:

```python
# tests/research_domain/test_cli.py
import pytest
from click.testing import CliRunner

from research_domain.cli import main
from tests.substrate.postgres_fixture import postgres_dsn


def test_append_then_show_reflects_the_appended_event(postgres_dsn):
    runner = CliRunner()

    append_result = runner.invoke(
        main,
        [
            "append",
            "claim.proposed",
            '{"claim_id": "claim-1", "source_id": "source-a", "text": "the sky is green"}',
            "--dsn", postgres_dsn,
            "--stream", "cli-test-stream",
        ],
    )
    assert append_result.exit_code == 0, append_result.output
    assert "Appended" in append_result.output

    show_result = runner.invoke(
        main,
        ["show", "source_coverage", "--dsn", postgres_dsn, "--stream", "cli-test-stream"],
    )
    assert show_result.exit_code == 0, show_result.output
    assert "source-a" in show_result.output


def test_append_rejects_invalid_json_payload(postgres_dsn):
    runner = CliRunner()

    result = runner.invoke(
        main,
        ["append", "claim.proposed", "not-json", "--dsn", postgres_dsn, "--stream", "cli-test-stream-2"],
    )
    assert result.exit_code != 0
    assert "Invalid JSON" in result.output
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/research_domain/test_cli.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'research_domain.cli'`

- [ ] **Step 3: Write `research_domain/cli.py`**

```python
# research_domain/cli.py
from __future__ import annotations
import asyncio
import json
import os

import click
from rich.console import Console
from rich.table import Table

from research_domain.runtime import ResearchRuntime

console = Console()


def _resolve_dsn(dsn: str | None) -> str:
    if dsn:
        return dsn
    env_dsn = os.environ.get("DATABASE_URL")
    if not env_dsn:
        raise click.ClickException("No --dsn given and DATABASE_URL is not set.")
    return env_dsn


@click.group()
def main() -> None:
    """research_domain: append events to the research stream and inspect projections."""


@main.command()
@click.argument("event_type")
@click.argument("payload_json")
@click.option("--dsn", default=None, help="Postgres DSN (defaults to DATABASE_URL env var)")
@click.option("--stream", default="research-stream", help="Event stream id")
def append(event_type: str, payload_json: str, dsn: str | None, stream: str) -> None:
    """Append EVENT_TYPE with PAYLOAD_JSON to the research stream."""
    try:
        payload = json.loads(payload_json)
    except json.JSONDecodeError as exc:
        raise click.ClickException(f"Invalid JSON payload: {exc}")

    resolved_dsn = _resolve_dsn(dsn)

    async def _run() -> None:
        runtime = ResearchRuntime(resolved_dsn, stream=stream)
        await runtime.connect()
        try:
            await runtime.append_event(event_type, payload)
        finally:
            await runtime.close()

    asyncio.run(_run())
    console.print(f"[green]Appended[/green] {event_type}")


@main.command()
@click.argument("projection_name")
@click.option("--dsn", default=None, help="Postgres DSN (defaults to DATABASE_URL env var)")
@click.option("--stream", default="research-stream", help="Event stream id")
def show(projection_name: str, dsn: str | None, stream: str) -> None:
    """Show the current value of PROJECTION_NAME."""
    resolved_dsn = _resolve_dsn(dsn)

    async def _run() -> dict:
        runtime = ResearchRuntime(resolved_dsn, stream=stream)
        await runtime.connect()
        try:
            await runtime.catch_up()
            return runtime.get_projection(projection_name)
        finally:
            await runtime.close()

    result = asyncio.run(_run())
    table = Table(title=projection_name)
    table.add_column("Key")
    table.add_column("Value")
    for key, value in result.items():
        table.add_row(str(key), str(value))
    console.print(table)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/research_domain/test_cli.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Register the console script**

In `pyproject.toml`, the `[project.scripts]` section currently reads:

```toml
[project.scripts]
novelizer = "novelizer.director.cli:main"
```

Change it to:

```toml
[project.scripts]
novelizer = "novelizer.director.cli:main"
research-domain = "research_domain.cli:main"
```

- [ ] **Step 6: Run the full `tests/research_domain/` suite as a regression check**

Run: `uv run pytest tests/research_domain/ -v`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add research_domain/cli.py tests/research_domain/test_cli.py pyproject.toml
git commit -m "feat(research-domain): add CLI (append, show) and register research-domain console script"
```

---

### Task 4: Final regression and mark the design spec implemented

**Files:**
- Modify: `docs/superpowers/specs/2026-07-22-research-domain-runtime-design.md` (status line)

**Interfaces:**
- No code interfaces produced — verification and documentation only.

- [ ] **Step 1: Run the full targeted regression suite for this branch**

Run: `uv run pytest tests/substrate/ tests/research_domain/ -v`
Expected: all pass (still targeted, not the full `tests/` suite — per Global Constraints).

- [ ] **Step 2: Verify the import-linter contract once more with the CLI and runtime files in place**

Run: `uv run lint-imports`
Expected: `Contracts: 1 kept, 0 broken.`

- [ ] **Step 3: Update the design spec's status line**

In `docs/superpowers/specs/2026-07-22-research-domain-runtime-design.md`, change:
```markdown
**Status: approved, not yet implemented.**
```
to:
```markdown
**Status: implemented (2026-07-22).**
```

- [ ] **Step 4: Commit**

```bash
git add docs/superpowers/specs/2026-07-22-research-domain-runtime-design.md
git commit -m "docs(research-domain): mark runtime + CLI spec implemented"
```
