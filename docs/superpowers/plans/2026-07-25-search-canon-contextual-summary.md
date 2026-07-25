# search_canon Contextual Summarization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give `search_canon` the agent's stated purpose and have it return a short grounded synthesis of the top hits' actual content above — never instead of — the existing hit lines.

**Architecture:** `novelizer/canon_fs/search.py` keeps its search + hit-line formatting responsibilities unchanged. A new module `novelizer/canon_fs/search_summary.py` owns everything about the LLM pass: excerpt gathering, the grounding prompt, the model call, and the "on any failure, return nothing" contract. `search.py` calls into it and prepends the result when it's non-empty. The summarizer's settings are read at *call* time through a `settings_provider` callable, because the runtime caches this tool for the process's lifetime.

**Tech Stack:** Python 3.13, pytest (async), LangChain (`langchain_core.messages`, `@tool`), deepagents backends, pydantic settings models.

## Global Constants

Copy these exact values; they are referenced by several tasks.

| name | value | where |
|---|---|---|
| `SEARCH_RESULT_CAP` | `20` | already in `search.py`, unchanged |
| `SUMMARY_SOURCE_CAP` | `5` | `search_summary.py` |
| `SUMMARY_BODY_LINES` | `120` | `search_summary.py` |
| `SUMMARY_MAX_TOKENS` | `400` | `search_summary.py` |

## Global Constraints

- **`purpose` is a required parameter.** There are exactly 13 existing
  `tool.ainvoke({"query": ...})` call sites across
  `tests/canon_fs/test_search.py` and `tests/canon_fs/test_search_description.py`.
  Task 4 updates all of them. Do not make `purpose` optional to avoid this work.
- **The hit-line block is never modified.** Not reordered, not filtered, not
  renumbered, not reworded. The summary is strictly prepended.
- **Summarizer failure is silent.** Any exception, timeout, or empty response
  returns the plain hit-line block with no `CONTEXT` header and no error text.
  A search that produced correct results must never fail because a nicety
  failed.
- **No new dependencies.** Everything needed is already imported somewhere in
  the repo.
- **Run tests from the worktree, never the main checkout** (project rule: a
  test run in the main checkout has caused a DB-lock incident).

## File Structure

- **Create** `novelizer/canon_fs/search_summary.py` — excerpt gathering, prompt,
  model call, failure contract. One responsibility: turn (query, purpose, hits)
  into a summary string or `""`.
- **Create** `tests/canon_fs/test_search_summary.py` — unit tests for the above.
- **Modify** `novelizer/canon_fs/search.py` — new signature, short-circuit,
  prepend, updated docstring.
- **Modify** `tests/canon_fs/test_search.py` — add `purpose` to call sites, add
  integration tests.
- **Modify** `tests/canon_fs/test_search_description.py` — add `purpose` to call
  sites, assert new prompt surface.
- **Modify** `novelizer/settings/models.py`, `layers.py`, `loader.py` — the
  `search_summarize` flag.
- **Create** `tests/settings/test_search_summarize_setting.py`.
- **Modify** `novelizer/runtime.py` — pass `backend`, `settings_provider`,
  `callbacks`.

---

### Task 1: The `search_summarize` settings flag

**Files:**
- Modify: `novelizer/settings/models.py` (near `outline_gate_enabled`, line ~78)
- Modify: `novelizer/settings/layers.py` (near line 81)
- Modify: `novelizer/settings/loader.py` (near line 63)
- Test: `tests/settings/test_search_summarize_setting.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `EffectiveSettings.search_summarize: bool` (default `True`),
  overridable from global config and story config. Task 3 and Task 4 read it.

This mirrors `outline_gate_enabled` exactly — that flag appears in all three
files, and this one follows the same path so it can be set the same ways.

- [ ] **Step 1: Write the failing test**

Create `tests/settings/test_search_summarize_setting.py`:

```python
"""The search_canon summarization kill switch.

Every semantic search costs an extra LLM call plus up to five file reads, on
the hot path of every pull-mode agent. This flag is how an operator turns that
bill off without a code change.
"""
from novelizer.settings.layers import GlobalConfig
from novelizer.settings.loader import StoryConfig
from novelizer.settings.models import EffectiveSettings


def test_defaults_to_on():
    assert EffectiveSettings().search_summarize is True


def test_global_config_can_turn_it_off():
    assert GlobalConfig(search_summarize=False).search_summarize is False


def test_global_config_leaves_it_unset_by_default():
    # None means "fall through to the built-in default", not "off".
    assert GlobalConfig().search_summarize is None


def test_story_config_can_turn_it_off():
    assert StoryConfig(search_summarize=False).search_summarize is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/settings/test_search_summarize_setting.py -v`
Expected: FAIL — pydantic raises on the unknown field, or `AttributeError:
'EffectiveSettings' object has no attribute 'search_summarize'`.

- [ ] **Step 3: Add the field to all three settings files**

In `novelizer/settings/models.py`, immediately after the `outline_gate_enabled`
field and its comment block (around line 78), add:

```python
    # search_canon contextual summarization: when True, a semantic search also
    # spends one LLM call plus up to five canon file reads to synthesize a
    # short grounded answer to the caller's stated purpose. Turn OFF to get the
    # bare ranked hit list -- identical to the pre-summarization behavior --
    # when the token bill matters more than the round-trips it saves.
    search_summarize: bool = True
```

In `novelizer/settings/layers.py`, in `GlobalConfig` next to
`outline_gate_enabled` (line ~81):

```python
    search_summarize: bool | None = None
```

In `novelizer/settings/loader.py`, in `StoryConfig` next to
`outline_gate_enabled` (line ~63):

```python
    search_summarize: bool | None = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/settings/test_search_summarize_setting.py -v`
Expected: 4 passed.

Then confirm nothing else broke:
Run: `pytest tests/settings/ -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add novelizer/settings/ tests/settings/test_search_summarize_setting.py
git commit -m "feat(settings): add search_summarize kill switch"
```

---

### Task 2: Excerpt gathering

**Files:**
- Create: `novelizer/canon_fs/search_summary.py`
- Test: `tests/canon_fs/test_search_summary.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `SUMMARY_SOURCE_CAP: int = 5`, `SUMMARY_BODY_LINES: int = 120`,
    `SUMMARY_MAX_TOKENS: int = 400`
  - `async def gather_excerpts(hits, backend, path_by_id, entity_lines) -> list[str]`
    — returns at most `SUMMARY_SOURCE_CAP` formatted excerpt blocks.
    `hits` is the list of hit objects (each has `.id`, `.kind`, `.title`).
    `backend` is anything with `async aread(path, limit=...) -> ReadResult`.
    `path_by_id` is `dict[str, str]` mapping record id → canon path.
    `entity_lines` is `dict[str, str]` mapping entity hit id → its already
    formatted inline line. Task 4 supplies all four.

Note on `aread`: it returns a `ReadResult` dataclass with `.error` and
`.file_data`. Convert with `file_data_to_string(result.file_data)`. There are
no line-number prefixes to strip — `slice_read_response` returns raw text and
numbering happens downstream in middleware.

- [ ] **Step 1: Write the failing test**

Create `tests/canon_fs/test_search_summary.py`:

```python
"""Excerpt gathering for search_canon's contextual summary.

The summarizer is only as good as what it is shown, and what it is shown must
be bounded: a 4k-word chapter body would blow the context this is meant to
save.
"""
from __future__ import annotations

from dataclasses import dataclass

from deepagents.backends.protocol import ReadResult
from deepagents.backends.utils import create_file_data

from novelizer.canon_fs.reads import TRUNCATION_MARKER
from novelizer.canon_fs.search_summary import (
    SUMMARY_BODY_LINES, SUMMARY_SOURCE_CAP, gather_excerpts,
)


@dataclass
class _Hit:
    id: str
    kind: str
    title: str


class _Backend:
    """Records every aread call so the tests can assert on the limit."""

    def __init__(self, bodies=None, errors=()):
        self.bodies = bodies or {}
        self.errors = set(errors)
        self.calls = []

    async def aread(self, path, offset=0, limit=2000):
        self.calls.append((path, limit))
        if path in self.errors:
            return ReadResult(error=f"File '{path}' not found.")
        return ReadResult(file_data=create_file_data(self.bodies.get(path, "")))


async def test_reads_bodies_for_file_backed_hits():
    hits = [_Hit("ch1", "chapter", "The Drowned Bell")]
    backend = _Backend({"/chapters/001-the-drowned-bell.md": "The bell rang."})
    out = await gather_excerpts(
        hits, backend, {"ch1": "/chapters/001-the-drowned-bell.md"}, {})
    assert len(out) == 1
    assert "The bell rang." in out[0]
    assert "The Drowned Bell" in out[0]
    assert "ch1" in out[0]


async def test_caps_the_number_of_bodies_read():
    hits = [_Hit(f"ch{i}", "chapter", f"C{i}") for i in range(20)]
    paths = {f"ch{i}": f"/chapters/{i}.md" for i in range(20)}
    backend = _Backend({p: "body" for p in paths.values()})
    out = await gather_excerpts(hits, backend, paths, {})
    assert len(out) == SUMMARY_SOURCE_CAP
    assert len(backend.calls) == SUMMARY_SOURCE_CAP


async def test_passes_the_body_line_limit():
    hits = [_Hit("ch1", "chapter", "One")]
    backend = _Backend({"/chapters/1.md": "body"})
    await gather_excerpts(hits, backend, {"ch1": "/chapters/1.md"}, {})
    assert backend.calls == [("/chapters/1.md", SUMMARY_BODY_LINES)]


async def test_a_read_error_does_not_lose_the_other_excerpts():
    hits = [_Hit("ch1", "chapter", "One"), _Hit("ch2", "chapter", "Two")]
    paths = {"ch1": "/chapters/1.md", "ch2": "/chapters/2.md"}
    backend = _Backend({"/chapters/2.md": "survived"}, errors=["/chapters/1.md"])
    out = await gather_excerpts(hits, backend, paths, {})
    assert len(out) == 1
    assert "survived" in out[0]


async def test_entity_hits_use_their_inline_line_and_read_nothing():
    hits = [_Hit("7", "entity", "The Salted Gull")]
    backend = _Backend()
    out = await gather_excerpts(
        hits, backend, {}, {"7": "(entity) [place] The Salted Gull — a tavern"})
    assert backend.calls == []
    assert "a tavern" in out[0]


async def test_fileless_kinds_contribute_title_only_and_read_nothing():
    # arcs have no backing file at all; briefs and promises have no
    # individually addressable one.
    hits = [_Hit("A-1", "arc", "Mateo's fall")]
    backend = _Backend()
    out = await gather_excerpts(hits, backend, {}, {})
    assert backend.calls == []
    assert "Mateo's fall" in out[0]


async def test_drops_the_truncation_notice():
    # sliced_read appends a SYSTEM NOTICE telling the READER to call read_file
    # again. Left in, the summarizer echoes that instruction into the CONTEXT
    # block, where it is nonsense addressed to the wrong party.
    body = f"real content\n[SYSTEM NOTICE — tool output] {TRUNCATION_MARKER}: you were shown lines 1-120 of 400."
    hits = [_Hit("ch1", "chapter", "One")]
    backend = _Backend({"/chapters/1.md": body})
    out = await gather_excerpts(hits, backend, {"ch1": "/chapters/1.md"}, {})
    assert "real content" in out[0]
    assert TRUNCATION_MARKER not in out[0]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/canon_fs/test_search_summary.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named
'novelizer.canon_fs.search_summary'`.

- [ ] **Step 3: Write the implementation**

Create `novelizer/canon_fs/search_summary.py`:

```python
"""The LLM pass behind search_canon's CONTEXT block.

search.py answers "what matched". This module answers "what does canon
actually say about it, given why you asked" -- one bounded model call over the
bodies of the top hits.

Everything here is best-effort by construction: `summarize` returns "" on any
failure, and search.py treats "" as "just send the hit lines". A search that
found the right records must never fail because the synthesis did.
"""
from __future__ import annotations

import logging

from deepagents.backends.utils import file_data_to_string

from novelizer.canon_fs.reads import TRUNCATION_MARKER

logger = logging.getLogger("novelizer.canon_fs.search_summary")

# How many hits get their bodies read. Five is enough to answer most "does
# canon say X" questions and bounded enough to stay cheap on a hot path.
SUMMARY_SOURCE_CAP = 5
# Per-file read window. A long chapter would otherwise blow the very context
# this feature exists to conserve.
SUMMARY_BODY_LINES = 120
# Generation cap. The prompt asks for ~120 words; this is the hard stop.
SUMMARY_MAX_TOKENS = 400


def _body_text(result) -> str:
    """ReadResult -> plain text, with sliced_read's truncation notice removed.

    That notice is addressed to an agent ("call read_file again with
    offset=..."), not to a summarizer. Passing it through invites the model to
    repeat an instruction aimed at the wrong party.
    """
    if result is None or getattr(result, "error", None):
        return ""
    text = file_data_to_string(result.file_data)
    marker = text.find(TRUNCATION_MARKER)
    if marker != -1:
        # Cut back to the start of the line the marker sits on.
        text = text[:text.rfind("\n", 0, marker) + 1]
    return text.strip()


async def gather_excerpts(hits, backend, path_by_id, entity_lines) -> list[str]:
    """Up to SUMMARY_SOURCE_CAP labelled excerpt blocks for the top hits.

    Entity hits carry their content inline already; fileless kinds (arc,
    brief, promise) contribute a title only. A read failure on one hit drops
    that hit and keeps the rest -- a partial excerpt set still summarizes
    usefully.
    """
    blocks: list[str] = []
    for hit in hits[:SUMMARY_SOURCE_CAP]:
        header = f"--- ({hit.kind}) '{hit.title}' [id: {hit.id}]"
        if hit.kind == "entity":
            line = entity_lines.get(hit.id, "")
            blocks.append(f"{header}\n{line}".strip())
            continue
        path = path_by_id.get(hit.id)
        if not path:
            # arc / brief / promise: no readable file. The title is still a
            # signal worth showing the summarizer.
            blocks.append(header)
            continue
        try:
            result = await backend.aread(path, limit=SUMMARY_BODY_LINES)
        except Exception:
            logger.debug("search summary: read failed for %s", path, exc_info=True)
            continue
        body = _body_text(result)
        if not body:
            continue
        blocks.append(f"{header}\n{body}")
    return blocks
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/canon_fs/test_search_summary.py -v`
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add novelizer/canon_fs/search_summary.py tests/canon_fs/test_search_summary.py
git commit -m "feat(search): gather bounded canon excerpts for summarization"
```

---

### Task 3: The grounded summarizer call

**Files:**
- Modify: `novelizer/canon_fs/search_summary.py`
- Test: `tests/canon_fs/test_search_summary.py`

**Interfaces:**
- Consumes: `gather_excerpts`, `SUMMARY_MAX_TOKENS` from Task 2;
  `settings.search_summarize` from Task 1.
- Produces:
  `async def summarize(query, purpose, excerpts, settings, callbacks=None) -> str`
  — returns the summary text, or `""` on any failure or when there is nothing
  to summarize. Task 4 calls this and prepends a `CONTEXT` header when the
  result is non-empty.

The model is built *inside* the call, from the `settings` passed in — mirroring
`novelizer/tui/tool_summarizer.py`, which does the same thing for the same
reason. This is what keeps a settings reload effective without rebuilding the
cached tool.

- [ ] **Step 1: Write the failing test**

Append to `tests/canon_fs/test_search_summary.py`:

```python
import novelizer.canon_fs.search_summary as summary_mod
from novelizer.canon_fs.search_summary import summarize


class _Settings:
    agent_model = "m"
    llm_base_url = "http://x"
    llm_api_key = "k"
    llm_max_tokens = 4096
    search_summarize = True


class _Response:
    def __init__(self, content):
        self.content = content


class _Model:
    """Captures the prompt; optionally raises."""

    def __init__(self, reply="canon says the debt stands.", boom=None):
        self.reply, self.boom, self.prompts = reply, boom, []

    async def ainvoke(self, messages):
        self.prompts.append(messages[-1].content)
        if self.boom:
            raise self.boom
        return _Response(self.reply)


def _patch_model(monkeypatch, model):
    monkeypatch.setattr(
        summary_mod, "build_chat_model", lambda *a, **k: model)
    return model


async def test_returns_the_models_summary(monkeypatch):
    model = _patch_model(monkeypatch, _Model())
    out = await summarize("the debt", "deciding ch12", ["--- excerpt"], _Settings())
    assert out == "canon says the debt stands."


async def test_prompt_carries_both_query_and_purpose(monkeypatch):
    model = _patch_model(monkeypatch, _Model())
    await summarize("the debt Mateo owes", "checking if it is repaid",
                    ["--- excerpt"], _Settings())
    prompt = model.prompts[0]
    assert "the debt Mateo owes" in prompt
    assert "checking if it is repaid" in prompt


async def test_prompt_carries_the_excerpts(monkeypatch):
    model = _patch_model(monkeypatch, _Model())
    await summarize("q", "p", ["--- (secret) 'The note'\nIlse holds it."],
                    _Settings())
    assert "Ilse holds it." in model.prompts[0]


async def test_model_failure_returns_empty_not_an_error_string(monkeypatch):
    # The search itself succeeded. Its results must still reach the agent.
    _patch_model(monkeypatch, _Model(boom=RuntimeError("502")))
    assert await summarize("q", "p", ["--- e"], _Settings()) == ""


async def test_empty_reply_returns_empty(monkeypatch):
    _patch_model(monkeypatch, _Model(reply="   \n  "))
    assert await summarize("q", "p", ["--- e"], _Settings()) == ""


async def test_no_excerpts_makes_no_model_call(monkeypatch):
    model = _patch_model(monkeypatch, _Model())
    assert await summarize("q", "p", [], _Settings()) == ""
    assert model.prompts == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/canon_fs/test_search_summary.py -v`
Expected: FAIL with `ImportError: cannot import name 'summarize'`.

- [ ] **Step 3: Write the implementation**

Add to the imports at the top of `novelizer/canon_fs/search_summary.py`:

```python
from agent_kit import build_chat_model
from langchain_core.messages import HumanMessage
```

Import `build_chat_model` at module scope (not inside the function) so the
tests can monkeypatch `search_summary.build_chat_model`.

Then append to the module:

```python
_PROMPT = """You are answering a question about a novel's canon using ONLY the \
excerpts below.

The agent searched for: {query}
They are asking because: {purpose}

EXCERPTS
{excerpts}

Write at most 120 words answering what canon says, as it bears on why they are \
asking. Rules:
- Assert nothing that is not in the excerpts above. No inference beyond what \
the text states.
- If the excerpts do not answer the purpose, say so plainly in one sentence. \
That is a useful answer, not a failure.
- Refer to records by their titles and ids as shown.
- Plain prose. No markdown, no headings, no bullet list.
"""


async def summarize(query, purpose, excerpts, settings, callbacks=None) -> str:
    """A short grounded synthesis of `excerpts`, or "" if anything goes wrong.

    Never raises. The caller treats "" as "send the hit lines alone", so a
    summarizer outage costs the agent a nicety and nothing else.
    """
    if not excerpts:
        return ""
    prompt = _PROMPT.format(
        query=query, purpose=purpose, excerpts="\n\n".join(excerpts))
    try:
        model = build_chat_model(
            settings.agent_model, settings.llm_base_url, settings.llm_api_key,
            temperature=0.0,
            max_tokens=min(SUMMARY_MAX_TOKENS, settings.llm_max_tokens),
            callbacks=callbacks,
        )
        response = await model.ainvoke([HumanMessage(content=prompt)])
    except Exception:
        # Debug, not warning: a degraded summary is invisible to the agent by
        # design, and this can fire on every search during an outage.
        logger.debug("search summary: model call failed", exc_info=True)
        return ""
    return str(response.content).strip()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/canon_fs/test_search_summary.py -v`
Expected: 13 passed (7 from Task 2 + 6 new).

- [ ] **Step 5: Commit**

```bash
git add novelizer/canon_fs/search_summary.py tests/canon_fs/test_search_summary.py
git commit -m "feat(search): grounded summarizer call with silent degradation"
```

---

### Task 4: Wire summarization into `search_canon`

**Files:**
- Modify: `novelizer/canon_fs/search.py`
- Modify: `tests/canon_fs/test_search.py` (all `ainvoke` call sites)
- Modify: `tests/canon_fs/test_search_description.py` (all `_tool(...)` call sites)

**Interfaces:**
- Consumes: `gather_excerpts`, `summarize`, `SUMMARY_SOURCE_CAP` from Tasks 2–3;
  `settings.search_summarize` from Task 1.
- Produces: `build_search_canon_tool(embedding_store, read_store, kg_store,
  backend=None, settings_provider=None, callbacks=None)` and the tool signature
  `search_canon(query: str, purpose: str, kinds: list[str] | None = None,
  summarize: bool = True)`. Task 5 wires the runtime to the new kwargs.

**Why `settings_provider` is a callable and not a `Settings`:**
`runtime._phase_a_toolkit()` runs exactly once during `start()`
(`novelizer/runtime.py:400`) and caches its result on `self._canon_backend` /
`self._canon_tools`. Every later `apply_settings` rebuild *reuses* those cached
tools. A `Settings` object captured at construction would freeze the model and
the kill switch at start-up values for the life of the process. Do not
"simplify" this to a plain settings argument.

- [ ] **Step 1: Write the failing test**

First, add `"purpose": "..."` to every existing `tool.ainvoke({...})` call in
`tests/canon_fs/test_search.py` — there are 13 such call sites across that file
and `test_search_description.py`. Any short human-readable string works, e.g.
`"purpose": "checking canon"`. These calls must keep passing unchanged
otherwise.

Then append to `tests/canon_fs/test_search.py`:

```python
import novelizer.canon_fs.search as search_mod


class _SummarySettings:
    agent_model = "m"
    llm_base_url = "http://x"
    llm_api_key = "k"
    llm_max_tokens = 4096
    search_summarize = True


class _NullBackend:
    async def aread(self, path, offset=0, limit=2000):
        from deepagents.backends.protocol import ReadResult
        from deepagents.backends.utils import create_file_data
        return ReadResult(file_data=create_file_data("body text"))


async def test_summarize_false_is_byte_identical_to_the_bare_hit_list(store, monkeypatch):
    """The regression anchor: opting out must reproduce the old output exactly."""
    ch = Chapter(id="ch1", title="The Drowned Bell", prose="The bell rang.")
    await store.upsert_chapter(ch)
    read = FakeReadStore(chapters=[ch])
    monkeypatch.setattr(
        search_mod, "summarize", _boom_summarize)  # must never be called
    tool = build_search_canon_tool(
        store, read, None, backend=_NullBackend(),
        settings_provider=lambda: _SummarySettings())
    out = await tool.ainvoke(
        {"query": "bell", "purpose": "p", "summarize": False})
    assert out == "(chapter) /chapters/001-the-drowned-bell.md — 'The Drowned Bell' [id: ch1]"


async def _boom_summarize(*a, **k):
    raise AssertionError("summarizer must not run")


async def test_kill_switch_off_skips_summarization(store, monkeypatch):
    ch = Chapter(id="ch1", title="One", prose="alpha")
    await store.upsert_chapter(ch)
    monkeypatch.setattr(search_mod, "summarize", _boom_summarize)

    class _Off(_SummarySettings):
        search_summarize = False

    tool = build_search_canon_tool(
        store, FakeReadStore(chapters=[ch]), None, backend=_NullBackend(),
        settings_provider=lambda: _Off())
    out = await tool.ainvoke({"query": "alpha", "purpose": "p"})
    assert not out.startswith("CONTEXT")


async def test_no_settings_provider_skips_summarization(store, monkeypatch):
    ch = Chapter(id="ch1", title="One", prose="alpha")
    await store.upsert_chapter(ch)
    monkeypatch.setattr(search_mod, "summarize", _boom_summarize)
    tool = build_search_canon_tool(store, FakeReadStore(chapters=[ch]), None)
    out = await tool.ainvoke({"query": "alpha", "purpose": "p"})
    assert not out.startswith("CONTEXT")


async def test_summary_is_prepended_and_hit_lines_survive_verbatim(store, monkeypatch):
    ch = Chapter(id="ch1", title="The Drowned Bell", prose="The bell rang.")
    await store.upsert_chapter(ch)
    read = FakeReadStore(chapters=[ch])

    async def _fake_summarize(query, purpose, excerpts, settings, callbacks=None):
        return "The bell tolled at dusk."

    monkeypatch.setattr(search_mod, "summarize", _fake_summarize)
    tool = build_search_canon_tool(
        store, read, None, backend=_NullBackend(),
        settings_provider=lambda: _SummarySettings())
    out = await tool.ainvoke({"query": "bell", "purpose": "deciding ch12"})
    assert out.startswith("CONTEXT (for: deciding ch12)")
    assert "The bell tolled at dusk." in out
    assert "RESULTS (cite these ids)" in out
    # the hit line is untouched
    assert "(chapter) /chapters/001-the-drowned-bell.md — 'The Drowned Bell' [id: ch1]" in out


async def test_summarizer_failure_degrades_to_the_bare_hit_list(store, monkeypatch):
    ch = Chapter(id="ch1", title="The Drowned Bell", prose="The bell rang.")
    await store.upsert_chapter(ch)

    async def _empty(query, purpose, excerpts, settings, callbacks=None):
        return ""

    monkeypatch.setattr(search_mod, "summarize", _empty)
    tool = build_search_canon_tool(
        store, FakeReadStore(chapters=[ch]), None, backend=_NullBackend(),
        settings_provider=lambda: _SummarySettings())
    out = await tool.ainvoke({"query": "bell", "purpose": "p"})
    assert out == "(chapter) /chapters/001-the-drowned-bell.md — 'The Drowned Bell' [id: ch1]"
    assert "CONTEXT" not in out


async def test_early_returns_never_reach_the_summarizer(store, monkeypatch):
    """Empty index, no results, and store errors all short-circuit."""
    monkeypatch.setattr(search_mod, "summarize", _boom_summarize)
    tool = build_search_canon_tool(
        store, FakeReadStore(), None, backend=_NullBackend(),
        settings_provider=lambda: _SummarySettings())
    out = await tool.ainvoke({"query": "anything", "purpose": "p"})
    assert out.startswith("Search unavailable")


async def test_kill_switch_is_read_at_call_time_not_construction(store, monkeypatch):
    """The runtime caches this tool for the process's lifetime, so a settings
    reload has to reach it without a rebuild."""
    ch = Chapter(id="ch1", title="One", prose="alpha")
    await store.upsert_chapter(ch)

    async def _fake_summarize(query, purpose, excerpts, settings, callbacks=None):
        return "summary"

    monkeypatch.setattr(search_mod, "summarize", _fake_summarize)
    live = _SummarySettings()
    tool = build_search_canon_tool(
        store, FakeReadStore(chapters=[ch]), None, backend=_NullBackend(),
        settings_provider=lambda: live)
    assert (await tool.ainvoke({"query": "alpha", "purpose": "p"})).startswith("CONTEXT")
    live.search_summarize = False   # operator flips it; no rebuild
    assert not (await tool.ainvoke({"query": "alpha", "purpose": "p"})).startswith("CONTEXT")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/canon_fs/test_search.py -v`
Expected: FAIL — `build_search_canon_tool() got an unexpected keyword argument
'backend'`, and the `purpose`-bearing calls fail validation.

- [ ] **Step 3: Write the implementation**

In `novelizer/canon_fs/search.py`, add to the imports:

```python
from novelizer.canon_fs.search_summary import gather_excerpts, summarize
```

Import `summarize` by name at module scope so tests can monkeypatch
`search_mod.summarize`.

Change the factory signature (line 13) to:

```python
def build_search_canon_tool(embedding_store, read_store, kg_store,
                            backend=None, settings_provider=None,
                            callbacks=None):
    """Factory so the tool closes over story-scoped stores (one tool
    instance per runner, mirroring how runners close over settings).

    settings_provider is a zero-arg callable, deliberately: runtime caches
    this tool for the life of the process (_phase_a_toolkit runs once in
    start()), so a Settings captured here would freeze the summarizer's
    model and kill switch at start-up values forever. Reading it per call is
    what makes the kill switch switchable.
    """
```

Change the tool signature (line 18) to:

```python
    @tool
    async def search_canon(query: str, purpose: str,
                           kinds: list[str] | None = None,
                           summarize: bool = True) -> str:
```

Note the local parameter `summarize` shadows the imported `summarize`
function inside the tool body. Import the function under an alias to avoid
the collision:

```python
from novelizer.canon_fs.search_summary import gather_excerpts
from novelizer.canon_fs import search_summary
```

and call it as `search_summary.summarize(...)`. Tests then monkeypatch
`search_mod.search_summary.summarize`. **Update the test file's monkeypatch
targets accordingly** — use
`monkeypatch.setattr(search_mod.search_summary, "summarize", ...)` in every
test from Step 1.

Then, replacing the current `return "\n".join(lines)` at line 97:

```python
        hit_lines = "\n".join(lines)
        settings = settings_provider() if settings_provider else None
        if not summarize or settings is None or not getattr(
                settings, "search_summarize", True):
            return hit_lines
        entity_lines = {
            h.id: line for h, line in zip(hits[:SEARCH_RESULT_CAP], lines)
            if h.kind == "entity"
        }
        excerpts = await gather_excerpts(
            hits[:SEARCH_RESULT_CAP], backend, path_by_id, entity_lines)
        text = await search_summary.summarize(
            query, purpose, excerpts, settings, callbacks=callbacks)
        if not text:
            return hit_lines
        return (f"CONTEXT (for: {purpose})\n{text}\n\n"
                f"RESULTS (cite these ids)\n{hit_lines}")
```

Guard: `gather_excerpts` needs a `backend`; if `backend is None`, skip
summarization and return `hit_lines` — add `or backend is None` to the
short-circuit condition.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/canon_fs/ -v`
Expected: all pass, including the pre-existing tests with `purpose` added.

- [ ] **Step 5: Commit**

```bash
git add novelizer/canon_fs/search.py tests/canon_fs/
git commit -m "feat(search): prepend contextual summary to search_canon results"
```

---

### Task 5: Tool description (prompt surface)

**Files:**
- Modify: `novelizer/canon_fs/search.py` (the `search_canon` docstring)
- Test: `tests/canon_fs/test_search_description.py`

**Interfaces:**
- Consumes: the signature from Task 4.
- Produces: nothing consumed by later tasks.

The docstring is the *only* thing that teaches agents to pass a real `purpose`.
An agent that passes `purpose="search"` gets a useless summary, so the
description has to say what a good purpose looks like.

- [ ] **Step 1: Write the failing test**

Append to `tests/canon_fs/test_search_description.py`, inside
`class TestDescription`:

```python
    def test_documents_the_purpose_argument(self):
        desc = _tool([]).description
        assert "purpose" in desc

    def test_says_purpose_is_the_decision_not_the_query(self):
        """An agent that echoes the query into purpose gets a useless summary."""
        desc = _tool([]).description.lower()
        assert "restate" in desc or "not a restatement" in desc

    def test_documents_the_summarize_opt_out(self):
        assert "summarize=False" in _tool([]).description

    def test_says_results_is_the_citation_source_of_truth(self):
        desc = _tool([]).description
        assert "RESULTS" in desc and "CONTEXT" in desc
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/canon_fs/test_search_description.py -v`
Expected: 4 FAIL on missing substrings.

- [ ] **Step 3: Update the docstring**

In `novelizer/canon_fs/search.py`, insert this into the `search_canon`
docstring, after the existing "Returns one line per hit…" paragraph and before
the "Path convention…" paragraph:

```
        Pass `purpose`: one sentence on the decision you are making, NOT a
        restatement of the query. "deciding whether Mateo's debt is still
        open before drafting ch12" is a purpose; "Mateo's debt" is not. The
        response leads with a CONTEXT block synthesizing the top hits'
        actual content against that purpose.

        The RESULTS block below CONTEXT is the source of truth for citation:
        ids and paths come from there, never from the prose summary. Pass
        summarize=False when you want the bare ranked index and intend to
        read the files yourself.
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/canon_fs/test_search_description.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add novelizer/canon_fs/search.py tests/canon_fs/test_search_description.py
git commit -m "docs(search): teach agents to pass a real purpose"
```

---

### Task 6: Runtime wiring

**Files:**
- Modify: `novelizer/runtime.py:303` (inside `_phase_a_toolkit`)
- Test: `tests/canon_fs/test_search_runtime_wiring.py` (create)

**Interfaces:**
- Consumes: the factory signature from Task 4.
- Produces: nothing.

- [ ] **Step 1: Write the failing test**

Create `tests/canon_fs/test_search_runtime_wiring.py`:

```python
"""The tool is only summarizing if the runtime actually hands it the pieces.

Wiring is exactly the kind of thing that looks done and silently is not: the
tool degrades to the bare hit list when backend/settings are missing, which is
indistinguishable from "summarization is off" unless something asserts it.
"""
import inspect

import novelizer.runtime as runtime_mod


def test_phase_a_toolkit_passes_backend_settings_and_callbacks():
    src = inspect.getsource(runtime_mod.NovelizerRuntime._phase_a_toolkit)
    assert "backend=backend" in src
    assert "settings_provider=lambda: self.settings" in src
    assert "callbacks=self._llm_callbacks" in src
```

If the runtime class is not named `NovelizerRuntime`, use the actual class
name — find it with `grep -n "_phase_a_toolkit" -B 40 novelizer/runtime.py |
grep "^.*class "`.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/canon_fs/test_search_runtime_wiring.py -v`
Expected: FAIL — the assertions find none of those substrings.

- [ ] **Step 3: Update the runtime**

In `novelizer/runtime.py`, replace line 303:

```python
        tools = [build_search_canon_tool(self.embeddings, self.read, self.kg_store)]
```

with:

```python
        # settings_provider, not settings: this toolkit is built once in
        # start() and cached, so the tool has to read the live settings on
        # every call or a reload would never reach it. callbacks matter too --
        # the summarizer's LLM calls are real spend and belong in the Engine
        # Room and the shared rate-limit pool like every other call.
        tools = [build_search_canon_tool(
            self.embeddings, self.read, self.kg_store,
            backend=backend, settings_provider=lambda: self.settings,
            callbacks=self._llm_callbacks)]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/canon_fs/test_search_runtime_wiring.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add novelizer/runtime.py tests/canon_fs/test_search_runtime_wiring.py
git commit -m "feat(runtime): wire search_canon summarization"
```

---

### Task 7: Full-suite verification

**Files:** none modified unless the suite finds something.

Per the project's standing rule, the full suite runs **once, at the end** — not
per task.

- [ ] **Step 1: Sweep for stale call sites**

Run: `grep -rn "build_search_canon_tool" --include="*.py" . | grep -v "\.venv"`
Confirm every call site either passes the new kwargs or deliberately omits them
(tests that construct a non-summarizing tool are fine).

Run: `grep -rn "search_canon(" --include="*.md" novelizer/ docs/`
Any agent prompt or skill doc showing a `search_canon(...)` example without
`purpose` is now teaching the wrong signature. Update those examples. Check
`novelizer/agents/prompts.py:18` in particular — it is the shared retrieval
note every tooled agent gets.

- [ ] **Step 2: Run the full suite from the worktree**

Run: `pytest -q`
Expected: no new failures.

Known pre-existing conditions — do **not** chase these, they are not from this
work:
- `tests/tui/` has ~9 failures on plain `main` and is additionally load-flaky.
  Compare against a `main` run of the identical scope before blaming this
  branch.
- A `chromadb` `DeprecationWarning` breaks any `-W error` run.

- [ ] **Step 3: Commit any fixes**

```bash
git add -A
git commit -m "fix: update search_canon call sites for the new signature"
```

---

## Self-Review

**Spec coverage:** signature → T4; flow steps 1–6 → T4 (search, hit lines,
short-circuit) + T2 (excerpts) + T3 (model call); grounding → T3; degradation →
T3 + T4; wiring incl. the `settings_provider` rationale → T4 + T6; tool
description → T5; constants → T2; all 13 spec test cases → T2 (7, 9, 11), T3
(4, 5, 6), T4 (1, 2, 3, 10, 13), T5 (12); cost/kill-switch → T1. No gaps.

**Placeholder scan:** none — every step carries runnable code or an exact
command.

**Type consistency:** `gather_excerpts(hits, backend, path_by_id, entity_lines)`
and `summarize(query, purpose, excerpts, settings, callbacks=None)` are
defined in T2/T3 and called with those exact arities in T4. The
`summarize` name collision between the tool's boolean parameter and the
module function is called out explicitly in T4 Step 3, with the alias fix and
the corresponding monkeypatch-target correction.
