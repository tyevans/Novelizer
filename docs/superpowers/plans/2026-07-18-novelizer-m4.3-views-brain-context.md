# M4.3 · Who-Knows-What & Causeway Views + Brain Context Injection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the M4 loop (COMPLETES the milestone). Author's prompt gains a live "who knows what" note (every non-revealed secret and which characters have learned it) and Editor's prompt gains a live "causal flags" note (paradox candidates), computed from the exact same pure functions (`novelizer.canon.secrets.knowledge_cell_state`, `novelizer.brain.paradoxes.find_paradoxes`) that two new Mission Control panes, Who-Knows-What and Causeway, render at display time — so the room's two views of "who knows the secret" and "what's a paradox" can never disagree. This lands the M4 milestone done-when: (a) a CI-verifiable mechanical chain proving the plumbing (seed a leak → `LeakDetector` flags it → `ContinuityChecker.run_once()` files a tagged retcon request → Who-Knows-What's render-time helper still shows the character as not-having-learned the secret), and (b) a `live_llm`-marked live-LLM smoke test proving the actual causal claim — an unprompted real Author, given only the injected who-knows-what note, declares a `uses` knowledge intent for a secret it wasn't told the character knows, and the resulting leak reaches the retcon queue.

**Architecture:** (1) **This sub-milestone is M3.3's exact sibling — same shape, new domain.** `novelizer/brain/context.py` (M3.3) gains two more pure note builders, `known_secrets_note()` (Author-facing) and `causal_flags_note()` (Editor-facing), following the identical conditional-string/empty-when-nothing-to-report/byte-identical-otherwise pattern already established by `stale_threads_note()`/`pacing_flags_note()`. Both are computed live inside `Author.poll()`/`Editor.poll()` (already fetching `ReadStore` data every call, per M3.3's precedent) and appended in `_summarize()`/`work()` exactly like `casting_note`/`personality`/`voices`/the M3.3 notes — last in the concatenation chain, so every M3.3 byte-identical-when-empty test keeps passing untouched. (2) **`known_secrets_note()` is deliberately not POV-scoped** (Locked decision #7): it lists every non-revealed secret and the characters who currently know it (`novelizer.canon.secrets.knowledge_cell_state` evaluated per character), because the note is injected before the chapter exists, when no POV has been chosen — this equips the Author to avoid a leak for *any* character it chooses to write. `causal_flags_note()` lists every M4.2 `find_paradoxes` candidate; **no causal-graph injection beyond flagged paradoxes** — the graph itself is never injected (Locked decision #7). (3) **The two new TUI widgets follow `ThreadBoard`/`StoryShape`'s exact pattern** (`Static` + a pure `*_line(...)` formatter + an async `refresh_from(read)` method), not `StoryBrowser`'s `Tree`-based drill-in pattern: `who_knows_what.py` renders one line per secret (its title, id, and either `REVEALED` or the sorted list of characters who currently know it, via the same `knowledge_cell_state` function the note builder uses), and `causeway.py` renders one line per declared causal edge (cause → effect, its note, and a `[PARADOX]` marker when the *same* `find_paradoxes` function from M4.2 flags it — paradoxes are never persisted or recomputed with separate logic, per the M4.3 row). Edges are rendered sorted by `cause_chapter_id` (a flat list ordered by chapter, satisfying "grouped by chapter" without inventing `Tree`-node plumbing M3.3 explicitly avoided for the same class of view). (4) **Both panes land in Mission Control's persistent left column**, as two more `Static` panes below `ThreadBoard`/`StoryShape`, each with its own refresh worker loop registered in `on_mount()` — matching M3.3's Task 7 verbatim. (5) **The live-LLM smoke test's design is the one genuinely new piece of judgment this plan makes** (see Task 9): the fixture seeds a secret already known to one character (`mara`) but *not* to a second character (`kestrel`) who appears in the room, then drives the real Author with the injected `known_secrets_note` as the only signal. For the Author to *plausibly* produce a leak unprompted, the note must name a specific secret and say who does *not* know it is silent — the Author is never told "don't let kestrel find out" (that would make avoidance the trained behavior, defeating the test); instead the fixture seeds a `DirectorSignal` nudging the Author to write a scene where `kestrel` is present and secrets are relevant, giving the real LLM a plausible narrative reason to have `kestrel` reference the secret without having declared a `learn` intent for `kestrel` first — reproducing a genuine, unforced leak. This is documented in Task 9's own docstring, not hidden in this summary.

**Tech Stack:** Python 3.13, `pydantic` v2, `textual`, `pytest`+`pytest-asyncio` (`asyncio_mode=auto`), `hypothesis>=6.156.6`; the `live_llm` pytest marker (registered in `pyproject.toml`, `addopts = "-m 'not live_llm'"`) for the live-LLM smoke test, following `tests/agents/test_author_live_llm.py`'s exact precedent.

## Global Constraints

- `novelizer/brain/context.py`'s functions are pure: no `ReadStore`, no I/O, inputs are plain lists/dicts of already-fetched domain objects (`SecretRecord`, `Character`, the `knowledge_matrix()` dict shape, `CausalEdgeRecord`, chapter-order list) — same constraint M3.3 established.
- Brain context is never persisted and never re-implemented outside `novelizer.canon.secrets.knowledge_cell_state` / `novelizer.brain.paradoxes.find_paradoxes` — the Who-Knows-What pane, the Causeway pane, and Author/Editor prompts all call the same functions, per M4.2's stated design intent (no separate paradox logic, no separate knowledge-cell logic).
- `_commit_knowledge_intents`/`_commit_causal_intents` signatures are unchanged from M4.1 — brain context injection only affects what agents are *told*, never how declared knowledge/causal intents are validated or committed.
- M2/M3.3 injection mechanics apply verbatim: each brain-context string is appended in `work()`/`_summarize()` only when non-empty; when every note is empty, the prompt is byte-identical to pre-M4.3 output. Every pre-existing `Author`/`Editor` test stays green untouched.
- TDD, black-box-first; property tests only where warranted (this milestone is mostly integration/wiring, matching M3.3's own mix — M4.1/M4.2 already own the property tests for the underlying folds).
- The M4 done-when has two parts and both are explicit, separately-graded tasks in this plan (Tasks 8 and 9) — the CI-verifiable chain is necessary but not sufficient; the `live_llm`-marked test is the milestone's true observation, per the doc's own framing.
- Backward compatibility: the existing test suite stays green throughout; `LeakDetector`, `ParadoxDetector`, `ContinuityChecker`, `Committer`/`GatingCommitter`, `_commit_knowledge_intents`/`_commit_causal_intents` are untouched by this plan except where a task adds tests (no production code change to those modules).

---

### Task 1: `novelizer/brain/context.py` gains `known_secrets_note()` and `causal_flags_note()`

**Files:**
- Modify: `novelizer/brain/context.py`
- Test: `tests/brain/test_context.py`

**Interfaces:**
- Consumes: `SecretRecord`, `Character`, `CausalEdgeRecord` (`novelizer.store.models`); `knowledge_cell_state` (`novelizer.canon.secrets`, M4.1); `find_paradoxes` (`novelizer.brain.paradoxes`, M4.2).
- Produces: `known_secrets_note(secrets: list[SecretRecord], characters: list[Character], matrix: dict[str, dict]) -> str` and `causal_flags_note(edges: list[CausalEdgeRecord], chapter_order: list[str]) -> str` — both return `""` when there's nothing to report, and a `\n\n`-prefixed block otherwise (matching `stale_threads_note`/`pacing_flags_note`'s exact conditional-block shape).

- [ ] **Step 1: Write the failing tests**

Append to `tests/brain/test_context.py`:

```python
from novelizer.brain.context import known_secrets_note, causal_flags_note
from novelizer.store.models import Character, SecretRecord, CausalEdgeRecord


def _character(id_, name):
    return Character(id=id_, name=name)


def test_known_secrets_note_empty_when_no_secrets():
    assert known_secrets_note([], [], {}) == ""


def test_known_secrets_note_omits_revealed_secrets():
    secret = SecretRecord(id="the-map", title="The Map Is Forged", revealed=True)
    assert known_secrets_note([secret], [], {"the-map": {"revealed": True, "known_by": set()}}) == ""


def test_known_secrets_note_lists_secret_id_and_known_characters():
    mara = _character("mara", "Mara")
    kestrel = _character("kestrel", "Kestrel")
    secret = SecretRecord(id="the-heir-lives", title="The Heir Lives")
    matrix = {"the-heir-lives": {"revealed": False, "known_by": {"mara"}}}
    note = known_secrets_note([secret], [mara, kestrel], matrix)
    assert note.startswith("\n\n")
    assert "the-heir-lives" in note
    assert "Mara" in note
    assert "Kestrel" not in note


def test_known_secrets_note_flags_secret_known_to_no_one():
    secret = SecretRecord(id="the-map", title="The Map Is Forged")
    matrix = {"the-map": {"revealed": False, "known_by": set()}}
    note = known_secrets_note([secret], [], matrix)
    assert "known to no one" in note


def test_causal_flags_note_empty_when_no_paradoxes():
    edges = [CausalEdgeRecord(cause_chapter_id="c1", effect_chapter_id="c2")]
    assert causal_flags_note(edges, ["c1", "c2"]) == ""


def test_causal_flags_note_lists_ordering_paradox():
    edges = [CausalEdgeRecord(cause_chapter_id="c2", effect_chapter_id="c1")]
    note = causal_flags_note(edges, ["c1", "c2"])
    assert note.startswith("\n\n")
    assert "c2" in note and "c1" in note and "ordering" in note
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/brain/test_context.py -v`
Expected: FAIL — `ImportError: cannot import name 'known_secrets_note'`.

- [ ] **Step 3: Implement**

Replace the full contents of `novelizer/brain/context.py`:

```python
from __future__ import annotations
from novelizer.brain.paradoxes import find_paradoxes
from novelizer.brain.sag_spike import detect_sag_spike
from novelizer.brain.staleness import stale_threads
from novelizer.canon.secrets import knowledge_cell_state
from novelizer.store.models import CausalEdgeRecord, Chapter, Character, SecretRecord, StructureScore, ThreadRecord


def stale_threads_note(threads: list[ThreadRecord], chapters: list[Chapter]) -> str:
    """Build the Author-facing prompt block naming every currently-stale
    thread and the id the Author must cite to touch it back (per M3.1's
    thread identity rule -- ids are never invented, only cited). Empty
    string when nothing is stale, so Author.work()'s prompt stays
    byte-identical to pre-M3.3 output whenever the brain has nothing to say.
    """
    stale = stale_threads(threads, chapters)
    if not stale:
        return ""
    lines = "\n".join(f"- {t.name} (id:{t.id})" for t in stale)
    return f"\n\nStale threads (consider touching one, citing its id exactly):\n{lines}"


def pacing_flags_note(scores: list[StructureScore]) -> str:
    """Build the Editor-facing prompt block naming every chapter the pure
    sag/spike detector has flagged. Empty string when nothing is flagged.
    """
    flags = detect_sag_spike(scores)
    if not flags:
        return ""
    lines = "\n".join(f"- chapter {chapter_id}: {flag}" for chapter_id, flag in flags.items())
    return f"\n\nPacing flags:\n{lines}"


def known_secrets_note(
    secrets: list[SecretRecord], characters: list[Character], matrix: dict[str, dict]
) -> str:
    """Build the Author-facing who-knows-what summary of every non-revealed
    secret and which characters currently know it (M4 Locked decision #7).

    Deliberately NOT POV-scoped: injected before the chapter exists, when no
    POV has been chosen (and no POV field exists in the chapter schema), so
    the only coherent form is the full summary -- it equips the Author to
    avoid a leak for *any* character it chooses to write. Revealed secrets
    are omitted (they can no longer leak). Empty string when there are no
    non-revealed secrets, so Author.work()'s prompt stays byte-identical to
    pre-M4.3 output whenever the brain has nothing to say.
    """
    names_by_id = {c.id: c.name for c in characters}
    lines = []
    for secret in secrets:
        if secret.revealed:
            continue
        known = sorted(
            names_by_id.get(cid, cid)
            for cid in names_by_id
            if knowledge_cell_state(matrix, secret.id, cid) == "known"
        )
        who = f"known only to {', '.join(known)}" if known else "known to no one"
        lines.append(f"- '{secret.id}' ({secret.title}) — {who}")
    if not lines:
        return ""
    return "\n\nSecrets and who knows them:\n" + "\n".join(lines)


def causal_flags_note(edges: list[CausalEdgeRecord], chapter_order: list[str]) -> str:
    """Build the Editor-facing paradox-candidate summary, calling the *same*
    find_paradoxes function M4.2's Continuity Checker and M4.3's Causeway
    pane use -- no separate paradox logic (M4.3 row). Empty string when
    nothing is flagged.
    """
    candidates = find_paradoxes(edges, chapter_order)
    if not candidates:
        return ""
    lines = "\n".join(
        f"- chapter {p.cause_chapter_id} -> chapter {p.effect_chapter_id} ({p.reason})" for p in candidates
    )
    return f"\n\nCausal flags:\n{lines}"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/brain/test_context.py -v`
Expected: PASS (all prior 6 M3.3 tests + 6 new). Then `uv run pytest tests/ -v` for the full suite green.

- [ ] **Step 5: Commit**

```bash
git add novelizer/brain/context.py tests/brain/test_context.py
git commit -m "feat: novelizer.brain.context — known_secrets_note/causal_flags_note prompt builders"
```

---

### Task 2: Author's prompt gains the known-secrets note

**Files:**
- Modify: `novelizer/agents/author.py`
- Test: `tests/agents/test_author.py`

**Interfaces:**
- Consumes: `known_secrets_note` (Task 1); `ReadStore.list_characters()` (existing, already in `ctx["characters"]`); `ReadStore.knowledge_matrix()` (existing, M4.1).
- Produces: no new public interface — `Author.poll()`'s ctx dict gains a `"knowledge_matrix"` key; `_summarize()` appends a conditional known-secrets block, empty-safe, after the existing `{brain}` (stale-threads) block.

- [ ] **Step 1: Write the failing tests**

Append to `tests/agents/test_author.py`:

```python
from novelizer.canon.events import SecretCreated, SecretLearned


async def test_author_prompt_includes_known_secrets_note_when_present(stack):
    events, proj, read, committer = stack
    await events.append(EventType.SECRET_CREATED, "the-heir-lives", SecretCreated(id="the-heir-lives", title="The Heir Lives"))
    await events.append(EventType.CHARACTER_CREATED, "mara", __import__("novelizer.store.models", fromlist=["Character"]).Character(id="mara", name="Mara"))
    await events.append(EventType.SECRET_LEARNED, "the-heir-lives", SecretLearned(id="the-heir-lives", character_id="mara"))
    await proj.catch_up()
    runner = FakeRunner(ChapterDraft(title="T", prose="P"))
    author = Author(runner, read, committer)
    ctx = await author.poll()
    await author.work(ctx)
    sent = runner.calls[-1]["messages"][0]["content"]
    assert "Secrets and who knows them" in sent
    assert "the-heir-lives" in sent and "Mara" in sent


async def test_author_prompt_omits_known_secrets_note_when_no_secrets(stack):
    events, proj, read, committer = stack
    runner = FakeRunner(ChapterDraft(title="T", prose="P"))
    author = Author(runner, read, committer)
    ctx = await author.poll()
    await author.work(ctx)
    sent = runner.calls[-1]["messages"][0]["content"]
    assert "Secrets and who knows them" not in sent
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/agents/test_author.py -v`
Expected: FAIL — `test_author_prompt_includes_known_secrets_note_when_present` fails with `assert "Secrets and who knows them" in sent` being false, since `_summarize` doesn't yet build or append the note.

- [ ] **Step 3: Implement**

In `novelizer/agents/author.py`, update the import and `poll()`/`_summarize()`:

```python
from __future__ import annotations
from novelizer.agents.base import BaseAgent, ChapterDraft, Runner
from novelizer.brain.context import known_secrets_note, stale_threads_note
from novelizer.canon.read_store import ReadStore
from novelizer.canon.committer import Committer
from novelizer.canon.events import EventType
from novelizer.canon.threads import TERMINAL_STATES
from novelizer.store.models import Chapter
```

```python
def _summarize(ctx: dict, casting_note: str = "", personality: str = "") -> str:
    world = "\n".join(f"- {e.title}: {e.body[:150]}" for e in ctx["world"][:10]) or "None yet."
    chars = "\n".join(f"- {c.name}: {c.traits} | arc: {c.arc_status}" for c in ctx["characters"][:8]) or "None yet."
    prev = "\n".join(f"- '{c.title}': {c.prose[:200]}" for c in ctx["previous"]) or "None yet."
    notes = "\n".join(f"Director: {s.body}" for s in ctx["signals"]) or "None."
    voice = f"\n\nWrite in this prose voice: {casting_note}" if casting_note else ""
    cast = f"\n\nIn character: {personality}" if personality else ""
    brain = stale_threads_note(ctx["threads"], ctx["chapters"])
    secrets = known_secrets_note(ctx["secrets"], ctx["characters"], ctx["knowledge_matrix"])
    return (
        f"World lore:\n{world}\n\nCharacters:\n{chars}\n\n"
        f"Previous chapters:\n{prev}\n\nDirector notes:\n{notes}{voice}{cast}{brain}{secrets}\n\nWrite the next chapter."
    )
```

```python
    async def poll(self) -> dict:
        chapters = await self._read.list_chapters()
        return {
            "world": await self._read.list_world_entries(),
            "characters": await self._read.list_characters(),
            "previous": chapters[-3:],
            "chapters": chapters,
            "signals": await self._read.list_unconsumed_signals(target_agent=self.name),
            "threads": await self._read.list_threads(),
            "secrets": await self._read.list_secrets(),
            "knowledge_matrix": await self._read.knowledge_matrix(),
        }
```

(Only the `known_secrets_note` import, the `"knowledge_matrix"` key in `poll()`, and the `secrets = ...`/`{secrets}` additions to `_summarize` are new; `Author.__init__`, `readiness`, `work`, `commit`, `run_once`, `build_author_runner` are unchanged. `{brain}{secrets}` places the new section immediately after the M3.3 stale-threads block, before `\n\nWrite the next chapter.`, matching the existing tail-append convention.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/agents/test_author.py -v`
Expected: PASS (all prior + 2 new). Then `uv run pytest tests/ -v` for the full suite green.

- [ ] **Step 5: Commit**

```bash
git add novelizer/agents/author.py tests/agents/test_author.py
git commit -m "feat: Author's prompt cites who-knows-what via novelizer.brain.context"
```

---

### Task 3: Editor's prompt gains the causal-flags note

**Files:**
- Modify: `novelizer/agents/editor.py`
- Test: `tests/agents/test_editor.py`

**Interfaces:**
- Consumes: `causal_flags_note` (Task 1); `ReadStore.list_causal_edges()` (existing, M4.1).
- Produces: no new public interface — `Editor.poll()`'s ctx dict gains a `"causal_edges"` key (`ctx["chapters"]` already exists for deriving chapter order); `Editor.work()` appends a conditional causal-flags block, empty-safe, after the existing `{pacing}` block.

- [ ] **Step 1: Write the failing tests**

Append to `tests/agents/test_editor.py`:

```python
from novelizer.canon.events import CausalEdgeDeclared


async def test_editor_prompt_includes_causal_flags_note_when_present(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
    await events.append(EventType.CHAPTER_CREATED, "c2", Chapter(id="c2", title="Two", prose="p"))
    await events.append(EventType.CAUSAL_EDGE_DECLARED, "c1",
                        CausalEdgeDeclared(cause_chapter_id="c2", effect_chapter_id="c1"))
    await proj.catch_up()
    runner = FakeRunner(EditorVerdict(verdict="approve", notes="clean"))
    agent = Editor(runner, read, committer)
    ctx = await agent.poll()
    await agent.work(ctx)
    sent = runner.calls[-1]["messages"][0]["content"]
    assert "Causal flags" in sent
    assert "c2" in sent and "c1" in sent and "ordering" in sent


async def test_editor_prompt_omits_causal_flags_note_when_no_edges(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
    await proj.catch_up()
    runner = FakeRunner(EditorVerdict(verdict="approve", notes="clean"))
    agent = Editor(runner, read, committer)
    ctx = await agent.poll()
    await agent.work(ctx)
    sent = runner.calls[-1]["messages"][0]["content"]
    assert "Causal flags" not in sent
    assert sent == f"Chapter title: One\n\nProse:\np"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/agents/test_editor.py -v`
Expected: FAIL — `test_editor_prompt_includes_causal_flags_note_when_present` fails with `assert "Causal flags" in sent` being false; `test_editor_prompt_omits_causal_flags_note_when_no_edges`'s exact-match assertion currently passes, pinning the byte-identical baseline the implementation must preserve.

- [ ] **Step 3: Implement**

In `novelizer/agents/editor.py`, update the import and `poll()`/`work()`:

```python
from __future__ import annotations
from novelizer.agents.base import BaseAgent, Runner
from novelizer.agents.schemas import EditorVerdict
from novelizer.brain.context import causal_flags_note, pacing_flags_note
from novelizer.canon.read_store import ReadStore
from novelizer.canon.committer import Committer
from novelizer.canon.events import EventType
from novelizer.canon.threads import TERMINAL_STATES
from novelizer.store.models import DirectorSignal, SignalKind, EditorialStatus
```

```python
    async def poll(self) -> dict:
        drafts = await self._read.list_chapters(status=EditorialStatus.draft)
        return {
            "target": drafts[0] if drafts else None,
            "threads": await self._read.list_threads(),
            "scores": await self._read.list_structure_scores(),
            "secrets": await self._read.list_secrets(),
            "chapters": await self._read.list_chapters(),
            "causal_edges": await self._read.list_causal_edges(),
        }
```

```python
    async def work(self, ctx: dict) -> EditorVerdict | None:
        ch = ctx["target"]
        if ch is None:
            return None
        voice = (
            f"\n\nEnforce this prose voice: {self._casting_note}; note any drift in your feedback."
            if self._casting_note
            else ""
        )
        cast = f"\n\nIn character: {self.personality}" if self.personality else ""
        voices = await self._character_voices_block(ch.character_ids)
        pacing = pacing_flags_note(ctx["scores"])
        chapter_order = [c.id for c in ctx["chapters"]]
        causal = causal_flags_note(ctx["causal_edges"], chapter_order)
        msg = f"Chapter title: {ch.title}\n\nProse:\n{ch.prose}{voice}{cast}{voices}{pacing}{causal}"
        result = await self._runner.ainvoke({"messages": [{"role": "user", "content": msg}]})
        return result.get("structured_response")
```

(Only the `causal_flags_note` import, the `"causal_edges"` key in `poll()`, and the `chapter_order`/`causal = ...`/`{causal}` additions to `work()` are new; `SYSTEM_PROMPT`, `Editor.__init__`, `readiness`, `_character_voices_block`, `commit`, `run_once`, `build_editor_runner` are unchanged. `{voice}{cast}{voices}{pacing}{causal}` places the new section last, so `test_editor_prompt_omits_causal_flags_note_when_no_edges`'s byte-identical exact-match assertion continues to hold whenever there's nothing to flag.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/agents/test_editor.py -v`
Expected: PASS (all prior + 2 new). Then `uv run pytest tests/ -v` for the full suite green.

- [ ] **Step 5: Commit**

```bash
git add novelizer/agents/editor.py tests/agents/test_editor.py
git commit -m "feat: Editor's prompt cites causal flags via novelizer.brain.context"
```

---

### Task 4: `WhoKnowsWhat` widget — pure line formatter + `Static` widget

**Files:**
- Create: `novelizer/tui/widgets/who_knows_what.py`
- Test: `tests/tui/test_who_knows_what.py`

**Interfaces:**
- Consumes: `knowledge_cell_state` (`novelizer.canon.secrets`, M4.1); `SecretRecord`/`Character` (`novelizer.store.models`); `ReadStore.list_secrets()`/`list_characters()`/`knowledge_matrix()` (existing).
- Produces: `who_knows_what_line(secret: SecretRecord, characters: list[Character], matrix: dict[str, dict]) -> str`; `WhoKnowsWhat(Static)` with `async def refresh_from(self, read) -> None`.

- [ ] **Step 1: Write the failing tests**

Create `tests/tui/test_who_knows_what.py`:

```python
from novelizer.tui.widgets.who_knows_what import who_knows_what_line
from novelizer.store.models import Character, SecretRecord


def test_revealed_secret_line_shows_revealed():
    secret = SecretRecord(id="the-map", title="The Map Is Forged", revealed=True)
    matrix = {"the-map": {"revealed": True, "known_by": set()}}
    line = who_knows_what_line(secret, [], matrix)
    assert "The Map Is Forged" in line and "REVEALED" in line


def test_secret_known_to_one_character_names_them():
    mara = Character(id="mara", name="Mara")
    kestrel = Character(id="kestrel", name="Kestrel")
    secret = SecretRecord(id="the-heir-lives", title="The Heir Lives")
    matrix = {"the-heir-lives": {"revealed": False, "known_by": {"mara"}}}
    line = who_knows_what_line(secret, [mara, kestrel], matrix)
    assert "Mara" in line and "Kestrel" not in line and "REVEALED" not in line


def test_secret_known_to_no_one_says_so():
    secret = SecretRecord(id="the-map", title="The Map Is Forged")
    matrix = {"the-map": {"revealed": False, "known_by": set()}}
    line = who_knows_what_line(secret, [], matrix)
    assert "no one" in line
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/tui/test_who_knows_what.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'novelizer.tui.widgets.who_knows_what'`.

- [ ] **Step 3: Implement**

Create `novelizer/tui/widgets/who_knows_what.py`:

```python
from __future__ import annotations
from textual.widgets import Static
from novelizer.canon.secrets import knowledge_cell_state
from novelizer.store.models import Character, SecretRecord


def who_knows_what_line(secret: SecretRecord, characters: list[Character], matrix: dict[str, dict]) -> str:
    if secret.revealed:
        state = "REVEALED"
    else:
        known = sorted(c.name for c in characters if knowledge_cell_state(matrix, secret.id, c.id) == "known")
        state = ", ".join(known) if known else "known to no one"
    return f"· {secret.title} (id:{secret.id})  [{state}]"


class WhoKnowsWhat(Static):
    async def refresh_from(self, read) -> None:
        secrets = await read.list_secrets()
        characters = await read.list_characters()
        matrix = await read.knowledge_matrix()
        lines = [who_knows_what_line(s, characters, matrix) for s in secrets]
        self.update("\n".join(lines) or "no secrets yet")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/tui/test_who_knows_what.py -v`
Expected: PASS (3 passed). Then `uv run pytest tests/ -v` for the full suite green.

- [ ] **Step 5: Commit**

```bash
git add novelizer/tui/widgets/who_knows_what.py tests/tui/test_who_knows_what.py
git commit -m "feat: WhoKnowsWhat widget — secret x character knowledge matrix as a flat grid"
```

---

### Task 5: `Causeway` widget — pure line formatter + `Static` widget

**Files:**
- Create: `novelizer/tui/widgets/causeway.py`
- Test: `tests/tui/test_causeway.py`

**Interfaces:**
- Consumes: `find_paradoxes` (`novelizer.brain.paradoxes`, M4.2); `CausalEdgeRecord`/`Chapter` (`novelizer.store.models`); `ReadStore.list_causal_edges()`/`list_chapters()` (existing).
- Produces: `causeway_line(edge: CausalEdgeRecord, is_paradox: bool) -> str`; `Causeway(Static)` with `async def refresh_from(self, read) -> None`.

- [ ] **Step 1: Write the failing tests**

Create `tests/tui/test_causeway.py`:

```python
from novelizer.tui.widgets.causeway import causeway_line
from novelizer.store.models import CausalEdgeRecord


def test_ordinary_edge_line_has_no_marker():
    edge = CausalEdgeRecord(cause_chapter_id="c1", effect_chapter_id="c2", note="sets up the reveal")
    line = causeway_line(edge, False)
    assert "c1" in line and "c2" in line and "sets up the reveal" in line
    assert "PARADOX" not in line


def test_paradox_edge_line_shows_marker():
    edge = CausalEdgeRecord(cause_chapter_id="c2", effect_chapter_id="c1")
    line = causeway_line(edge, True)
    assert "PARADOX" in line
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/tui/test_causeway.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'novelizer.tui.widgets.causeway'`.

- [ ] **Step 3: Implement**

Create `novelizer/tui/widgets/causeway.py`:

```python
from __future__ import annotations
from textual.widgets import Static
from novelizer.brain.paradoxes import find_paradoxes
from novelizer.store.models import CausalEdgeRecord


def causeway_line(edge: CausalEdgeRecord, is_paradox: bool) -> str:
    marker = "  [PARADOX]" if is_paradox else ""
    return f"· chapter {edge.cause_chapter_id} → chapter {edge.effect_chapter_id}: {edge.note}{marker}"


class Causeway(Static):
    async def refresh_from(self, read) -> None:
        edges = await read.list_causal_edges()
        chapters = await read.list_chapters()
        chapter_order = [c.id for c in chapters]
        paradox_pairs = {
            (p.cause_chapter_id, p.effect_chapter_id) for p in find_paradoxes(edges, chapter_order)
        }
        ordered = sorted(edges, key=lambda e: (e.cause_chapter_id, e.effect_chapter_id))
        lines = [
            causeway_line(e, (e.cause_chapter_id, e.effect_chapter_id) in paradox_pairs) for e in ordered
        ]
        self.update("\n".join(lines) or "no causal edges yet")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/tui/test_causeway.py -v`
Expected: PASS (2 passed). Then `uv run pytest tests/ -v` for the full suite green.

- [ ] **Step 5: Commit**

```bash
git add novelizer/tui/widgets/causeway.py tests/tui/test_causeway.py
git commit -m "feat: Causeway widget — causal chains grouped by chapter with live paradox flags"
```

---

### Task 6: Wire `WhoKnowsWhat`/`Causeway` into `NovelizerApp.compose()`

**Files:**
- Modify: `novelizer/tui/app.py`
- Modify: `novelizer/tui/app.tcss`
- Test: `tests/tui/test_app_layout.py`

**Interfaces:**
- Consumes: `WhoKnowsWhat`, `Causeway` (Tasks 4, 5).
- Produces: no new public interface — `NovelizerApp.compose()` yields two new panes, `#who_knows_what` and `#causeway`; `on_mount` starts two new refresh-loop workers.

- [ ] **Step 1: Write the failing test**

Append to `tests/tui/test_app_layout.py`:

```python
@pytest.mark.asyncio
async def test_mission_control_shows_who_knows_what_and_causeway_panes():
    from novelizer.canon.events import EventType, SecretCreated, SecretLearned, CausalEdgeDeclared
    from novelizer.store.models import Chapter, Character

    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    settings = Settings(db_path=path, projector_interval=0.1)
    rt = Runtime(settings, runners=_runners())
    await rt.start()
    for name in ["world_architect", "character_keeper", "author", "editor", "continuity_checker", "retconner", "structure_analyst"]:
        rt.scheduler.pause_agent(name)
    try:
        await rt.events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
        await rt.events.append(EventType.CHARACTER_CREATED, "mara", Character(id="mara", name="Mara"))
        await rt.events.append(EventType.SECRET_CREATED, "the-heir-lives", SecretCreated(id="the-heir-lives", title="The Heir Lives"))
        await rt.events.append(EventType.SECRET_LEARNED, "the-heir-lives", SecretLearned(id="the-heir-lives", character_id="mara"))
        await rt.events.append(EventType.CHAPTER_CREATED, "c2", Chapter(id="c2", title="Two", prose="p"))
        await rt.events.append(EventType.CAUSAL_EDGE_DECLARED, "c1",
                               CausalEdgeDeclared(cause_chapter_id="c2", effect_chapter_id="c1"))
        await rt.projector.catch_up()
        app = NovelizerApp(rt)
        async with app.run_test() as pilot:
            from textual.widgets import Static
            assert app.query_one("#who_knows_what", Static) is not None
            assert app.query_one("#causeway", Static) is not None
            await pilot.pause(0.5)
            wkw_text = str(app.query_one("#who_knows_what", Static).renderable)
            causeway_text = str(app.query_one("#causeway", Static).renderable)
            assert "The Heir Lives" in wkw_text and "Mara" in wkw_text
            assert "c2" in causeway_text and "c1" in causeway_text and "PARADOX" in causeway_text
    finally:
        await rt.close(); os.unlink(path)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/tui/test_app_layout.py::test_mission_control_shows_who_knows_what_and_causeway_panes -v`
Expected: FAIL — `textual.css.query.NoMatches` (no widget with id `#who_knows_what`).

- [ ] **Step 3: Implement**

In `novelizer/tui/app.py`, add the imports, two new `compose()` panes, two new worker loops, and their `on_mount` registration:

```python
from novelizer.tui.widgets.roster import AgentRoster
from novelizer.tui.widgets.browser import StoryBrowser
from novelizer.tui.widgets.browser_model import detail_text
from novelizer.tui.widgets.proposals_model import pending_lines
from novelizer.tui.widgets.thread_board import ThreadBoard
from novelizer.tui.widgets.story_shape import StoryShape
from novelizer.tui.widgets.who_knows_what import WhoKnowsWhat
from novelizer.tui.widgets.causeway import Causeway
```

```python
    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="body"):
            with Vertical(id="left"):
                yield RichLog(highlight=False, markup=False, id="feed")
                yield AgentRoster(id="roster")
                yield Static("no pending proposals", id="proposals")
                yield ThreadBoard("no threads yet", id="thread_board")
                yield StoryShape("no chapters scored yet", id="story_shape")
                yield WhoKnowsWhat("no secrets yet", id="who_knows_what")
                yield Causeway("no causal edges yet", id="causeway")
            with Vertical(id="right"):
                yield StoryBrowser("Story", id="browser")
                yield Static("Select an item to view details.", id="detail")
        yield Static("AUTONOMY: loading…", id="statusbar")
        # compact=True drops Input's default tall border, which would consume
        # both edges of the single row #command gets and leave 0 content lines.
        yield Input(id="command", placeholder="command… (seed/focus/pause/resume)", compact=True)
        yield Footer()

    async def on_mount(self) -> None:
        self.run_worker(self._projector_loop(), exclusive=False)
        self.run_worker(self._scheduler_loop(), exclusive=False)
        self.run_worker(self._feed_loop(), exclusive=False)
        self.run_worker(self._roster_loop(), exclusive=False)
        self.run_worker(self._browser_loop(), exclusive=False)
        self.run_worker(self._proposals_loop(), exclusive=False)
        self.run_worker(self._statusbar_loop(), exclusive=False)
        self.run_worker(self._thread_board_loop(), exclusive=False)
        self.run_worker(self._story_shape_loop(), exclusive=False)
        self.run_worker(self._who_knows_what_loop(), exclusive=False)
        self.run_worker(self._causeway_loop(), exclusive=False)
```

```python
    async def _who_knows_what_loop(self) -> None:
        while True:
            try:
                await self.query_one("#who_knows_what", WhoKnowsWhat).refresh_from(self.runtime.read)
            except Exception as e:
                self._report_worker_error("who_knows_what", e)
            await asyncio.sleep(1.0)

    async def _causeway_loop(self) -> None:
        while True:
            try:
                await self.query_one("#causeway", Causeway).refresh_from(self.runtime.read)
            except Exception as e:
                self._report_worker_error("causeway", e)
            await asyncio.sleep(1.0)
```

(Only the two new imports, the two new `compose()` yields, the two new `run_worker(...)` calls in `on_mount`, and the two new loop methods are new; every other method is unchanged.)

In `novelizer/tui/app.tcss`, add CSS for the two new panes (after `#story_shape`):

```css
#who_knows_what { height: auto; max-height: 8; border: round $secondary; }
#causeway { height: auto; max-height: 8; border: round $secondary; }
```

(Every existing rule is unchanged; these two lines are new.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/tui/test_app_layout.py -v`
Expected: PASS (all prior + 1 new). Then `uv run pytest tests/ -v` for the full suite green (re-runs `test_app_smoke.py`/`test_app_resilience.py`/`test_app_commands.py` too, confirming the two new always-running worker loops don't destabilize any existing TUI test).

- [ ] **Step 5: Commit**

```bash
git add novelizer/tui/app.py novelizer/tui/app.tcss tests/tui/test_app_layout.py
git commit -m "feat: wire WhoKnowsWhat/Causeway panes into Mission Control"
```

---

### Task 7: Regression — brain-context injection leaves prompts byte-identical when the brain has nothing to report

**Files:**
- Test: `tests/agents/test_author.py`, `tests/agents/test_editor.py`

**Interfaces:** none produced — this task pins the byte-identical-when-empty contract explicitly across every optional section now composed in one prompt (four sections for Author: voice/cast/stale-threads/known-secrets; five for Editor: voice/cast/voices/pacing/causal), as an aggregate regression check.

- [ ] **Step 1: Write the tests**

Append to `tests/agents/test_author.py`:

```python
async def test_author_prompt_byte_identical_to_pre_m4_3_shape_when_brain_silent(stack):
    events, proj, read, committer = stack
    runner = FakeRunner(ChapterDraft(title="T", prose="P"))
    author = Author(runner, read, committer)
    ctx = await author.poll()
    await author.work(ctx)
    sent = runner.calls[-1]["messages"][0]["content"]
    expected = (
        "World lore:\nNone yet.\n\nCharacters:\nNone yet.\n\n"
        "Previous chapters:\nNone yet.\n\nDirector notes:\nNone.\n\nWrite the next chapter."
    )
    assert sent == expected
```

Append to `tests/agents/test_editor.py`:

```python
async def test_editor_prompt_byte_identical_to_pre_m4_3_shape_when_brain_silent(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
    await proj.catch_up()
    runner = FakeRunner(EditorVerdict(verdict="approve", notes="clean"))
    agent = Editor(runner, read, committer)
    ctx = await agent.poll()
    await agent.work(ctx)
    sent = runner.calls[-1]["messages"][0]["content"]
    assert sent == "Chapter title: One\n\nProse:\np"
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `uv run pytest tests/agents/test_author.py tests/agents/test_editor.py -v`
Expected: PASS immediately (Tasks 2/3 already guarantee this; these are aggregate pins, not new implementation).

- [ ] **Step 3: Commit**

```bash
git add tests/agents/test_author.py tests/agents/test_editor.py
git commit -m "test: pin byte-identical Author/Editor prompts when M4.3 brain context has nothing to report"
```

---

### Task 8: M4.3 done-when, part (a) — the CI-verifiable mechanical chain

**Files:**
- Test: `tests/agents/test_continuity_checker.py`

**Interfaces:** none produced — this is the doc's stated part-(a) black-box chain, composing M4.1/M4.2's landed pieces (`SecretCreated`, `LeakDetector`, `ContinuityChecker.run_once()`, `LEAK_SOURCE_TAG`, `list_retcon_requests`) with Task 4's `who_knows_what_line` into the exact sequence M4-knowledge-and-cause.md's done-when (a) describes. No production code changes.

- [ ] **Step 1: Write the test**

Append to `tests/agents/test_continuity_checker.py`:

```python
async def test_m4_3_done_when_mechanical_chain_leak_flagged_and_widget_still_shows_unknown(stack):
    """The M4.3 done-when, part (a): seed a secret (secret.created), a
    character who has NOT learned it, and a committed secret.referenced
    event naming that character using the secret in a chapter -> assert
    LeakDetector (M4.2) flags it -> drive ContinuityChecker.run_once() with
    a FakeRunner preset to return no LLM-found contradictions -> assert the
    resulting retcon_request.created event lands via the Committer, its
    description starting with LEAK_SOURCE_TAG -> assert it appears in
    list_retcon_requests(status=open) -> assert the Who-Knows-What widget's
    render-time helper still shows the character as not-having-learned the
    secret (the leak is flagged, not silently resolved). No live model call.
    """
    from novelizer.tui.widgets.who_knows_what import who_knows_what_line
    from novelizer.store.models import Character

    events, proj, read, committer = stack
    await events.append(EventType.SECRET_CREATED, "the-heir-lives",
                        SecretCreated(id="the-heir-lives", title="The Heir Lives"))
    await events.append(EventType.CHARACTER_CREATED, "kestrel", Character(id="kestrel", name="Kestrel"))
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
    await events.append(EventType.SECRET_REFERENCED, "the-heir-lives",
                        SecretReferenced(id="the-heir-lives", character_id="kestrel", chapter_id="c1"))
    await proj.catch_up()

    # Step 1: LeakDetector flags it deterministically (no LLM), via the same
    # poll() the Continuity Checker uses.
    agent = ContinuityChecker(FakeRunner(ContinuityOutput()), read, committer)
    ctx = await agent.poll()
    from novelizer.brain.leaks import find_leaks
    leaks = find_leaks(ctx["secret_references"], ctx["knowledge_matrix"])
    assert len(leaks) == 1 and leaks[0].character_id == "kestrel"

    # Step 2: run_once() with a FakeRunner that finds nothing on its own
    # still files a tagged retcon request via the Committer.
    await agent.run_once()
    await proj.catch_up()
    open_reqs = await read.list_retcon_requests(status=RetconStatus.open)
    leak_reqs = [r for r in open_reqs if r.description.startswith(LEAK_SOURCE_TAG)]
    assert len(leak_reqs) == 1
    assert "the-heir-lives" in leak_reqs[0].description and "kestrel" in leak_reqs[0].description

    # Step 3: it's visible in the open retcon queue.
    assert leak_reqs[0].status == RetconStatus.open

    # Step 4: the Who-Knows-What widget's render-time helper still shows
    # Kestrel as not having learned the secret -- the leak is flagged, not
    # silently resolved.
    secret = await read.get_secret("the-heir-lives")
    characters = await read.list_characters()
    matrix = await read.knowledge_matrix()
    line = who_knows_what_line(secret, characters, matrix)
    assert "Kestrel" not in line
    assert "known to no one" in line
```

- [ ] **Step 2: Run the test to verify it passes**

Run: `uv run pytest tests/agents/test_continuity_checker.py::test_m4_3_done_when_mechanical_chain_leak_flagged_and_widget_still_shows_unknown -v`
Expected: PASS — every piece it composes (`find_leaks`/M4.2, `run_once`/`LEAK_SOURCE_TAG`/M4.2, `who_knows_what_line`/Task 4) was already implemented and tested in isolation; this test is the integration proof, not new implementation. If it fails, the failure points at exactly which link in the chain broke — fix that task's implementation, not this test.

- [ ] **Step 3: Run the full suite**

Run: `uv run pytest tests/ -v`
Expected: all tests green.

- [ ] **Step 4: Commit**

```bash
git add tests/agents/test_continuity_checker.py
git commit -m "test: M4.3 done-when part (a) — CI-verifiable leak-flagged-to-widget-still-unknown mechanical chain"
```

---

### Task 9: M4.3 done-when, part (b) — the `live_llm`-marked live-LLM smoke test

**Files:**
- Create: `tests/agents/test_author_leak_live_llm.py`

**Interfaces:** none produced — a new `live_llm`-marked test file, excluded from the default run (`addopts = "-m 'not live_llm'"`) and run manually or in an environment with a live OpenAI-compatible endpoint, following `tests/agents/test_author_live_llm.py`'s exact precedent.

- [ ] **Step 1: Write the test**

Create `tests/agents/test_author_leak_live_llm.py`:

```python
"""M4 done-when, part (b): the true observation for M4, per
docs/submilestones/M4-knowledge-and-cause.md's own framing -- a
FakeRunner-driven test (see test_m4_3_done_when_mechanical_chain_... in
tests/agents/test_continuity_checker.py) only proves the pipe is connected,
not that a real LLM will act on what flows through it under live conditions.

Design of the fixture (documented here since the spec deliberately left this
open, per the M4.3 dispatch instructions): the real Author must produce the
secret.referenced event itself, via its own knowledge_intents "uses"
declaration -- LeakDetector cannot be fed a synthetic reference here, or the
test would no longer be proving that a real agent's structured output causes
a leak. For that to be *plausible* for a real LLM to produce unforced:

1. A secret ('the-heir-lives') is seeded already known to one character
   (Mara) but NOT to a second character (Kestrel) who is also active in the
   room -- both characters exist so the Author has someone to write about
   who does *not* know the secret.
2. The Author is given no director signal or manual prompt beyond what the
   room already injects: known_secrets_note() (Task 2) tells it, verbatim,
   "'the-heir-lives' (The Heir Lives) — known only to Mara", which is enough
   information for a scene involving Kestrel to plausibly reference the
   secret without the Author having first declared a `learn` intent for
   Kestrel -- exactly the shape of an unforced leak.
3. No fixture data or prompt text tells the Author to *avoid* a leak, or to
   write about Kestrel and the secret together -- the note states the
   knowledge fact only, and the Author is free to write any chapter; if it
   chooses to have Kestrel reference the secret, that's the room's existing
   injected context alone producing the leak, unprompted.

Requires the configured OpenAI-compatible LLM endpoint (`Settings().llm_base_url`)
to be reachable and serving the model named by NOVELIZER_AUTHOR_MODEL (see
README's Configuration section / docs/examples/config.example.toml). Run explicitly with:
uv run pytest -m live_llm tests/agents/test_author_leak_live_llm.py -v

This test is inherently non-deterministic (it depends on a real model's
narrative choices) -- a single failing run does not necessarily mean the
plumbing is broken; re-run, and if it fails consistently across several
runs, treat that as a real signal the injected note text needs
strengthening (see known_secrets_note() in novelizer/brain/context.py).
"""
import os
import tempfile
import pytest
from novelizer.settings import EffectiveSettings as Settings
from novelizer.canon.event_store import EventStore
from novelizer.canon.projector import Projector
from novelizer.canon.read_store import ReadStore
from novelizer.canon.committer import Committer
from novelizer.canon.events import EventType, SecretCreated, SecretLearned
from novelizer.agents.author import Author, build_author_runner
from novelizer.agents.continuity_checker import ContinuityChecker, build_continuity_checker_runner
from novelizer.brain.leaks import LEAK_SOURCE_TAG
from novelizer.store.models import Character, RetconStatus


@pytest.fixture
async def stack():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    events = EventStore(path)
    await events.init()
    proj = Projector(events, path)
    await proj.init()
    read = ReadStore(path)
    await read.init()
    yield events, proj, read, Committer(events)
    await read.close()
    await proj.close()
    await events.close()
    os.unlink(path)


@pytest.mark.live_llm
async def test_real_author_and_continuity_checker_catch_an_unprompted_leak(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHARACTER_CREATED, "mara", Character(id="mara", name="Mara"))
    await events.append(EventType.CHARACTER_CREATED, "kestrel", Character(id="kestrel", name="Kestrel"))
    await events.append(EventType.SECRET_CREATED, "the-heir-lives",
                        SecretCreated(id="the-heir-lives", title="The Heir Lives"))
    await events.append(EventType.SECRET_LEARNED, "the-heir-lives",
                        SecretLearned(id="the-heir-lives", character_id="mara"))
    await proj.catch_up()

    settings = Settings()
    author = Author(build_author_runner(settings), read, committer)
    await author.run_once()
    await proj.catch_up()

    checker = ContinuityChecker(build_continuity_checker_runner(settings), read, committer)
    await checker.run_once()
    await proj.catch_up()

    open_reqs = await read.list_retcon_requests(status=RetconStatus.open)
    leak_reqs = [r for r in open_reqs if r.description.startswith(LEAK_SOURCE_TAG)]
    assert leak_reqs, (
        "The real Author, given only the injected known_secrets_note (Mara knows "
        "'the-heir-lives', no one else does) and no other prompting, did not "
        "produce a chapter whose declared knowledge_intents caused a leak the "
        "real Continuity Checker then caught. See this file's module docstring "
        "for the fixture design and troubleshooting note."
    )
```

- [ ] **Step 2: Confirm it's excluded from the default run**

Run: `uv run pytest tests/ -v`
Expected: all tests green, and `tests/agents/test_author_leak_live_llm.py::test_real_author_and_continuity_checker_catch_an_unprompted_leak` does not appear in the run (excluded by `addopts = "-m 'not live_llm'"`).

- [ ] **Step 3: Manually verify against a live endpoint (documented, not CI-run)**

With a live OpenAI-compatible endpoint serving `NOVELIZER_AUTHOR_MODEL`/`NOVELIZER_AGENT_MODEL`:

```bash
uv run pytest -m live_llm tests/agents/test_author_leak_live_llm.py -v
```

Expected: PASS, confirming a planted knowledge leak is auto-caught and routed to the retcon queue with no manual prompting beyond the room's own injected context — this is the M4 milestone's stated done-when, verbatim. Record the result (pass/fail, model used, and if it failed, whether a retry passed) when this task is executed — CI cannot prove this per the doc's own framing; a documented manual run stands in for CI here exactly as M1–M3's own live-LLM checks did.

- [ ] **Step 4: Commit**

```bash
git add tests/agents/test_author_leak_live_llm.py
git commit -m "test: M4 done-when part (b) — live_llm-marked unprompted-leak-to-retcon-queue smoke test"
```

---

### Task 10: Docs — mark M4.3 and M4 complete, document Who-Knows-What/Causeway and the M4 brain context

**Files:**
- Modify: `docs/submilestones/M4-knowledge-and-cause.md`
- Modify: `docs/MILESTONES.md`
- Modify: `README.md`

**Interfaces:** none (documentation only).

- [ ] **Step 1: Update the sub-milestone table**

In `docs/submilestones/M4-knowledge-and-cause.md`, change the M4.3 row's `Status` cell from `not started` to `complete`.

- [ ] **Step 2: Update the parent milestone table**

In `docs/MILESTONES.md`, change the M4 row's `Status` cell from `⬜ not started` to `✅ complete`.

- [ ] **Step 3: Extend the README**

In `README.md`, add a new subsection immediately after the "Secret & causal-edge ledgers (Story Brain, Phase 2)" subsection added in M4.1 (before the `## Architecture` heading):

```markdown
### Who-Knows-What & Causeway, and M4 brain context in prompts

Mission Control's left column gains two more live panes: **Who-Knows-What**
(every secret, whether it's `REVEALED`, and — if not — the sorted list of
characters who currently know it, or `known to no one`) and **Causeway**
(every declared causal edge as a `cause → effect` line, grouped by cause
chapter, with a `PARADOX` marker whenever the *same* `find_paradoxes`
function from the leak/paradox analyzers flags it). Both read straight from
canon and call the exact same pure functions
(`novelizer.canon.secrets.knowledge_cell_state`,
`novelizer.brain.paradoxes.find_paradoxes`) that build the notes injected
into the Author's and Editor's prompts — so the room's two views of "who
knows the secret" or "what's a paradox" can never disagree.

The Author sees a **who-knows-what** note listing every non-revealed secret
and which characters currently know it — deliberately not scoped to a
point-of-view character, since the note is injected before the chapter
exists and no POV has been chosen yet; the Editor sees a **causal flags**
note naming paradox candidates. Both notes are empty, and the prompt is
byte-identical to a story with no Story Brain knowledge/causal signal,
whenever there's nothing to report — following the exact conditional-
injection pattern the M3.3 stale-threads/pacing-flags notes already
established.

A planted knowledge leak — a character's dialogue or action referencing a
secret they were never told, self-declared via the Author's own
`knowledge_intents` — is auto-caught by the deterministic leak analyzer and
routed to the retcon queue with no manual intervention, tagged
`[source: leak_detector]` for traceability; a causal edge that violates
chapter ordering or closes a cycle is auto-caught the same way, tagged
`[source: paradox_detector]`. This closes the M4 milestone.
```

- [ ] **Step 4: Commit**

```bash
git add docs/submilestones/M4-knowledge-and-cause.md docs/MILESTONES.md README.md
git commit -m "docs: mark M4.3 and M4 complete; document Who-Knows-What/Causeway and M4 brain context injection"
```

---

### Task 11: Full-suite verification

**Files:** none (verification only).

**Interfaces:** none.

- [ ] **Step 1: Run the full test suite from a clean stash**

```bash
git stash -u
uv run pytest tests/ -v
git stash pop
```

Expected: with the stash applied (pre-M4.3 `master`), the M4.1/M4.2 suite is green and none of this plan's new test files exist. After `git stash pop`, confirm the working tree is back to the fully-implemented state.

- [ ] **Step 2: Run the full suite on the completed branch**

Run: `uv run pytest tests/ -v`
Expected: every test green, including all M4.3 tests from Tasks 1–8 and 10. The `live_llm`-marked test from Task 9 is excluded by default and not counted in this run.

- [ ] **Step 3: Confirm no regressions in test count**

Run: `uv run pytest tests/ --collect-only -q | tail -5`
Expected: the collected test count has grown by exactly the number of new tests added in Tasks 1–8 and 10 (no test silently dropped or skipped).

---

## Self-Review

**Spec coverage against the M4.3 row, its two-part done-when, and Locked decisions #7/#8 in `docs/submilestones/M4-knowledge-and-cause.md`:**
- "`novelizer/tui/widgets/who_knows_what.py` (renders the secret × character matrix as a grid, reading `KnowledgeProjection` rows)" — Task 4 (`who_knows_what_line` reads `SecretRecord` + `knowledge_matrix()`, the M4.1 `KnowledgeProjection` accessor).
- "`novelizer/tui/widgets/causeway.py` (renders the causal graph as cause→effect chains grouped by chapter, calling the *same* `ParadoxDetector` function from M4.2 at render time via a small `ReadStore`-backed helper — paradoxes are never persisted as a projection field or recomputed with separate logic)" — Task 5 (`Causeway.refresh_from` imports `find_paradoxes` directly, sorts by `cause_chapter_id` for the "grouped by chapter" requirement, no separate paradox logic).
- "wired into `NovelizerApp.compose()`" — Task 6.
- "`novelizer/brain/context.py` gains two more note builders — `known_secrets_note()` ... deliberately not POV-scoped ... and `causal_flags_note()`" — Task 1, following the exact M3.3 conditional/empty-safe pattern; Locked decision #7's exact rationale (injected before POV exists) is restated in the docstring.
- "following the exact M3.3 pattern (conditional string, empty when nothing to report, byte-identical output otherwise)" — Tasks 2/3 (injection) and Task 7 (aggregate byte-identical pin).
- Done-when (a), the CI-verifiable mechanical chain, described clause-by-clause in the doc's "M4.3 done-when, in full" section — Task 8 implements every clause in the stated order: seed secret + non-learning character + committed `secret.referenced` → `LeakDetector` flags it → `ContinuityChecker.run_once()` with `FakeRunner` (no LLM findings) → `retcon_request.created` via `Committer`, description starting with `LEAK_SOURCE_TAG` → visible in `list_retcon_requests(status=open)` → Who-Knows-What widget's render-time helper still shows the character as not-having-learned.
- Done-when (b), the `live_llm`-marked smoke check, "the milestone's true done-when observation" — Task 9, with the fixture design (secret known to one seeded character, not another, no manual prompt beyond the injected note) explicitly reasoned through and documented in the test file's own module docstring, per the dispatch instructions.
- Locked decision #8 ("(a) is necessary but not sufficient; (b) is the milestone's true observation") — restated in this plan's Goal and in Task 9's docstring.

**Design decisions the M4.3 dispatch left open, resolved here (flagged explicitly):**
1. **Causeway's "grouped by chapter" is satisfied by sorting the flat edge list by `cause_chapter_id`**, not by building a `Tree`-node hierarchy — consistent with M3.3's explicit rejection of `StoryBrowser`'s `Tree` pattern for flat/scored lists (Thread Board, Story Shape) in favor of `AgentRoster`'s `Static`+pure-formatter pattern, which this plan follows for both new widgets.
2. **The live-LLM smoke test's fixture and prompting design** (Task 9) — reasoned through in full in the task's own docstring: the real Author must self-declare the leak via its own `knowledge_intents`, so the fixture seeds asymmetric knowledge (Mara knows, Kestrel doesn't, both active) and relies solely on `known_secrets_note()`'s injected text to make an unforced leak plausible, with no fixture text nudging the Author toward or away from a leak.
3. **Widget architecture and Mission Control placement**: `WhoKnowsWhat`/`Causeway` follow `ThreadBoard`/`StoryShape`'s exact `Static`+pure-line-formatter+`refresh_from(read)` shape and land as two more persistent left-column panes (not a dedicated drill-in view), mirroring M3.3's Task 5–7 precedent and its stated done-when scope ("wired into `NovelizerApp.compose()`" only).

**Placeholder scan:** every task's Step 3 shows complete code — full new files (`who_knows_what.py`, `causeway.py`, `test_author_leak_live_llm.py`) or exact before/after snippets anchored to the current file contents (re-read from `master` immediately before writing this plan: `events.py`, `read_store.py`, `leaks.py`, `paradoxes.py`, `continuity_checker.py`, `secrets.py`, `base.py`, `schemas.py`, `author.py`, `editor.py`, `context.py`, `thread_board.py`, `story_shape.py`, `app.py`, `app.tcss`, `models.py`, and `tests/tui/test_app_layout.py`, `tests/agents/test_continuity_checker.py`, `tests/agents/test_author_live_llm.py`, `tests/agents/test_author.py`). No "similar to Task N", no `...` elisions, no TODOs.

**Type consistency:** `known_secrets_note(secrets: list[SecretRecord], characters: list[Character], matrix: dict[str, dict]) -> str` (Task 1) matches `Author.poll()`'s `ctx["secrets"]`/`ctx["characters"]`/`ctx["knowledge_matrix"]` types (Task 2) and `who_knows_what_line`'s parameters (Task 4) exactly — all three read the identical `knowledge_matrix()` shape (`{secret_id: {"revealed": bool, "known_by": set[str]}}`) via `knowledge_cell_state`. `causal_flags_note(edges: list[CausalEdgeRecord], chapter_order: list[str]) -> str` (Task 1) matches `Editor.poll()`'s `ctx["causal_edges"]`/derived `chapter_order` (Task 3) and `Causeway.refresh_from`'s `find_paradoxes(edges, chapter_order)` call (Task 5) exactly — one `find_paradoxes` signature, three call sites, no drift.

**DDD/SOLID:**
- Single Responsibility: `novelizer/brain/context.py` only builds prompt-note strings; `who_knows_what.py`/`causeway.py` only render; `Author`/`Editor.poll()` are the only places that fetch `ReadStore` data for the brain; `NovelizerApp` only wires and refreshes.
- Open/Closed: `Author.poll()`/`_summarize()` and `Editor.poll()`/`work()` each gain one new dict key and one new conditional append, following the exact M3.3 precedent — no existing section's logic is touched. `NovelizerApp.compose()`/`on_mount()` each gain new, additive lines; no existing pane or loop is modified.
- Dependency Inversion / bounded context: `novelizer/brain/` and `novelizer/canon/secrets.py` remain pure, `ReadStore`-free analysis layers; `Author`/`Editor` depend on them only through plain function calls over data they already fetch; `WhoKnowsWhat`/`Causeway` depend on `ReadStore` and these pure functions only, never on agent or `ContinuityChecker` internals.
- Event sourcing: no new event types or projections in this plan — every rendered value (secret knowledge, causal edges) already flows from the M4.1/M4.2 event log through existing projections.

**Backward-compatibility check:** `Author`/`Editor`'s prompts are byte-identical whenever the M4.3 brain notes have nothing to report — guaranteed by Tasks 2/3's "omits" tests and pinned again in aggregate by Task 7; every pre-existing `Author`/`Editor` test constructs fixtures with no secrets/no causal edges beyond what M4.1's own tests already cover, so none of them exercise the new sections unexpectedly. `NovelizerApp.compose()`'s two new panes and `on_mount()`'s two new workers are additive; `tests/tui/test_app_smoke.py`, `test_app_resilience.py`, and `test_app_commands.py` (none of which assert on `#who_knows_what`/`#causeway` or a fixed pane count) are re-run in full by every task's Step 4/5 and are unaffected. `LeakDetector`, `ParadoxDetector`, `ContinuityChecker`, `Committer`/`GatingCommitter`, `_commit_knowledge_intents`/`_commit_causal_intents` are untouched by this plan.
