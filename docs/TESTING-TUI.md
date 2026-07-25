# Testing the TUI (and running the full suite without losing a night to it)

Practical notes from the Mission Control design-pass work (2026-07-18/19), where two
full-suite runs sat "blocked" for 30 minutes and ~12 hours respectively. Most of this
is generic to the repo's test setup; the war story at the bottom explains the specific
hang signature so nobody re-diagnoses it from scratch.

## The layers

- **Pure model tests** (`tests/tui/test_*_model.py`, `test_roster.py`, `test_identity.py`):
  plain functions in, `rich.Text`/strings out. No Textual runtime, milliseconds each.
  This is where almost all visual logic lives and where new rendering behavior gets its
  red/green tests first. Assert on `.plain` for content and on `.spans` for styling —
  widgets are thin enough that everything visual is checkable here.
- **tui_kit contract/model/widget tests** (`tests/tui_kit/test_contracts.py`,
  `test_run_model.py`, `test_widgets.py`, `test_roster.py`): the extracted
  rendering-kit layer, tested at three distinct altitudes inside one directory.
  `test_contracts.py` checks Protocol conformance — a `_FakeTheme` stub satisfying
  `AgentTheme` (`glyph`/`label`/`style`/`verb`) — plus that the bus-event dataclasses
  (`RunStarted`, `TokenDelta`, `ToolCallStarted`, ...) are frozen with the fields
  callers expect. `test_run_model.py` drives `apply_bus_item`/`route_agent`/
  `vitals_line` and friends as pure functions over `LiveRunState`: construct a bus
  item and a fake clock, apply it, assert on the resulting dataclass — no Textual
  runtime involved. `test_widgets.py` mounts the real Textual widgets (`ActivityStrip`,
  `EngineRoom`, `LiveStreamPanel`) inside a minimal `App`/`ComposeResult` harness
  defined right in the test file, then queries rendered `Static`/`DataTable` content
  after `await pilot.pause()`. `test_roster.py` uses Hypothesis (`@given`) to
  property-test `roster_glyphs`/`roster_summary` formatting across generated agent
  rosters. This is now where new tui_kit rendering behavior gets its red/green tests
  first — distinct from `tests/tui`'s app-level pure model tests, which still own
  `app.py`/`chat_screen.py`-specific logic.
- **Widget/pilot tests** (`test_app_layout.py`, `test_approval_screen.py`, etc.):
  Textual's `run_test()` pilot drives the real app headlessly. These are seconds each;
  the whole `tests/tui` suite is ~100s. Pilot tests cover wiring only (bindings, screen
  stack, loops writing into the right widget) — not rendering detail. As of c87fee5
  these exercise `app.py`/`chat_screen.py` as wired onto `tui_kit`, so a wiring
  regression can now originate in either the app layer or the tui_kit widgets it
  composes.
- **Screenshot verification** (manual, not in CI): copy a real story DB, run the app
  with no-op fake runners under a pilot, `app.save_screenshot()` → SVG → cairosvg PNG.
  Ty's `stories/` churns between sessions — always `ls stories/` for a current DB and
  always copy it; never point the app at a live DB.

## The gates

- Run the TUI suite as: `uv run pytest tests/tui tests/tui_kit -q -W error`
- **Zero warnings is a hard gate** (`-W error`). Textual deprecations and un-awaited
  coroutines surface as failures, on purpose.
- `live_llm`-marked tests are deselected by `addopts = "-m 'not live_llm'"` in
  `pyproject.toml`; a full run reporting "N deselected" is normal.

## Why the suite is as fast as it is (2026-07-22)

A "tests are slow, must be doing real inference" investigation found **zero live
inference** in the default run (`live_llm` deselection works) and two real costs,
both now fixed — keep them fixed:

- **SQLite fsync dominated everything.** `db.connect` opens WAL databases at
  `synchronous=FULL`, so every event append fsyncs; property tests appending
  thousands of events ran at ~19% CPU (pure disk wait). `tests/conftest.py` now
  patches `db.connect` to add `PRAGMA synchronous=OFF` for test DBs only.
  Measured: worst property test 19.7s → 2.6s, canon/brain/telemetry scope
  170s → 32s, identical Hypothesis example counts. Don't "fix" slow property
  tests by cutting `max_examples` — check for I/O wait first (`time` the run;
  low CPU% = waiting, not computing).
- **A Docker pgvector container per test.** `postgres_dsn` used to `docker run`
  + `pg_isready`-poll + `docker stop` around every test (~4s setup, up to 10s
  teardown, ~25 tests ≈ 2 min). It's now a session-scoped container
  (`pg_container`, surfaced in `tests/conftest.py`) with a throwaway
  `CREATE DATABASE` per test (~100ms) and `DROP ... WITH (FORCE)` teardown.
  Measured: substrate+research_domain scope 142s → 14s. New postgres tests
  should just take `postgres_dsn` as before; per-database isolation is
  equivalent to the old per-container isolation (extensions like pgvector are
  per-database and were always created by the code under test). For how to
  consume `postgres_dsn`/`pg_container` from a new domain's tests, see
  [substrate/README.md#testing-your-domain](../substrate/README.md#testing-your-domain);
  this section is just the internals.

  One readiness detail to leave alone: the container is polled with the
  `_psql` helper in `tests/substrate/postgres_fixture.py`, which runs
  `psql -h 127.0.0.1 ... SELECT 1` inside the container — and the
  `-h 127.0.0.1` is load-bearing. The pgvector image's init sequence brings up
  a *temporary* postgres that listens on the unix socket only, runs its init
  scripts, then restarts as the real server that listens on TCP. Socket-based
  `pg_isready` reports ready during that init window, before anything accepts
  TCP connections, so a `-h 127.0.0.1 ... SELECT 1` is the only probe that
  proves readiness the way tests actually connect (via the mapped TCP port).
  Don't "simplify" the helper back to `pg_isready` — startup goes flaky.

## Running the FULL suite (read this before you background it)

The full suite (`uv run pytest -q -W error`) takes ~2 minutes of CPU but has a known
failure mode where the *process outlives the test run*: chromadb's Rust core spawns a
large pool of `tokio-rt-worker` threads (observed: 112), and under some orderings the
interpreter wedges at or near shutdown — all tests done, process asleep on a futex,
forever. Two rules follow:

> **The thread pile-up itself was a leak, and it is fixed (2026-07-25).** The 112
> threads were not inherent to chromadb: nothing ever closed the Chroma client.
> `Runtime.close()` released read/projector/telemetry/events/kg_store but not
> `embeddings`, and `EmbeddingStore.close()` was `pass  # chromadb PersistentClient
> auto-flushes` — so every `finally: await rt.close()` in the suite freed nothing,
> and each started Runtime leaked its whole ~15-20-thread tokio pool. chromadb's own
> `Client.close()` docstring calls releasing it "particularly important for
> PersistentClient to avoid SQLite file locking issues".
>
> Two changes: `EmbeddingStore.close()` now closes the client (idempotent, since
> callers close from `finally`) and `Runtime.close()` calls it; and because ~20 test
> sites construct an `EmbeddingStore` directly and drop it, an autouse
> `_release_chroma_systems` fixture in `tests/conftest.py` stops the systems each
> test created. Measured over `tests/store tests/brain` (~290 tests), threads at
> exit went **1909 total / 1627 tokio → 32 total / 16 tokio**; over N
> `Runtime.start()/close()` cycles, 2/5/10 cycles went 54/111/206 threads → a flat
> 16. Regression tests: `tests/test_runtime_closes_resources.py` and
> `tests/store/test_embedding_store_lifecycle.py` (the thread-growth one is the load-
> bearing assertion — it fails on any reintroduced per-Runtime leak).
>
> A *separate* mid-run wedge in `tests/test_runtime.py` survives this fix and is
> still open: the run stops partway through that file with the main thread suspended
> in `selectors.select` (so an awaited coroutine, not CPU work) and only ~32 tokio
> threads alive, i.e. no longer thread exhaustion. It reproduces on plain `main`
> too, so it predates the fixes above; `tests/test_runtime.py` passes in isolation
> (57 passed, ~170s). `Runtime.start()` is not the culprit on its own — probed with
> `socket.socket.connect` patched to raise, start() with tools enabled makes **zero**
> connection attempts. Until it is diagnosed, verify with
> `--ignore=tests/test_runtime.py` plus a separate isolated run of that file.

1. **Never pipe a long pytest run through `tail`/`head`.** The pipe only flushes on
   EOF; if the process wedges at exit you get an empty file and zero visibility into
   1000 tests that may all have passed. Redirect to a file instead:

   ```sh
   timeout -s KILL 1500 uv run pytest -v -W error \
       -o faulthandler_timeout=120 > /tmp/fullsuite.log 2>&1
   ```

2. **Always wrap in `timeout -s KILL`** and set `-o faulthandler_timeout=120`:
   pytest's built-in faulthandler then dumps *every thread's stack* into the log if any
   single test stalls >120s, which distinguishes "a test is hung" from "the suite
   finished but the process won't die". With `-v` output going straight to the log, the
   last line always names the last test started.

### Diagnosing a wedged run (no sudo, `ptrace_scope=1`, so no py-spy)

- `ps -o etime,time`: huge elapsed + tiny CPU (~90s) means the suite *finished its
  work* and is wedged, not slow.
- `cat /proc/<pid>/task/*/comm | sort | uniq -c` — a pile of `tokio-rt-worker`
  threads fingerprints the chromadb shutdown wedge.
- `ls -l /proc/<pid>/fd` — open `data_level0.bin`/`header.bin` etc. are chroma
  segment files; deleted-but-open pytest tmpdirs tell you which tests touched chroma.
- `/proc/<pid>/net/tcp` is **namespace-wide**, not per-process — don't mistake other
  processes' connections (e.g. live novelizer sessions on the same box) for test
  network activity. Match the socket inode numbers from `/proc/<pid>/fd` instead.
- The chroma test file (`tests/agents/test_base.py -k theme_intents`) exits cleanly in
  isolation; the wedge only reproduces in larger runs, so bisect with directory
  subsets, each under `timeout`.

### Pilot tests are load-sensitive (2026-07-19)

Under heavy machine load — a live `novelizer` session eating a core, another
worktree's Claude session running pytest, load average ≥ ~5 — the pilot tests fail
*en masse* with `aiosqlite` `RuntimeError('Event loop is closed')` teardown races
surfacing as `PytestUnhandledThreadExceptionWarning` ExceptionGroups (fatal under
`-W error`), plus stray chromadb `DeprecationWarning`s from fixture setup ordering.
The same files pass when the box is quiet. `tests/tui_kit/test_widgets.py`'s harness
is the same `App.run_test()` pilot machinery under the hood, so treat it as equally
load-sensitive — don't diagnose a red `test_widgets.py` as a regression without
first checking load, the same as any other pilot file. Before diagnosing a red pilot
suite as a regression:

1. `uptime` and `ps -eo pid,etime,time,args --sort=-time | head` — look for a live
   `novelizer` process or another worktree's pytest.
2. Re-run the *same failing files* at the branch's base commit in a scratch worktree
   (`git worktree add /tmp/base-check <base>`); identical failures = environment,
   not code. Remove the scratch worktree after.
3. The pure suites (`tests/brain`, `tests/canon`, `tests/tui/test_*_model.py`) are
   load-immune — green there plus base-parity on the pilot failures is enough to
   clear a rendering-only change; re-run the pilot suite when the box is quiet.

**Compare identical scopes, and count the load you are generating yourself
(2026-07-20).** Two traps caught a full agent-prompt merge for half an hour:

- *Your own background runs are the load.* Several concurrent `pytest` runs from
  one Claude session reproduce the symptom exactly — 8 pilot failures at
  `tests/tui` in 273s, then 275/275 green in 133s once the others finished, same
  commit. Wall-clock roughly doubling versus a known-good run is the tell. Wait
  for your own runs to drain before believing a red pilot suite.
- *Scope must match on both sides.* Running four files at base (16 tests, green)
  against the whole `tests/tui` directory on the branch (275 tests, 8 red) looks
  like a regression and is not a comparison. Run the same path at both commits;
  a single pilot test also passes alone while failing in a directory run, so
  isolation proves nothing either way.

## Pilot-test conventions worth keeping

- Widget visibility asserts via the `display` property work whether visibility is set
  inline or via CSS classes — but *setting* `widget.display = ...` writes the inline
  style layer, which silently out-ranks every stylesheet rule (e.g. mode-scoped
  `#body.engine ... { display: none }`). Toggle classes from Python; let the
  stylesheet own `display`. Test the interaction (mode + state) explicitly — a test
  for each flag alone will pass while the combination is broken.
- Fake time/agents: pilot tests inject no-op fake runners; loops read settings every
  cycle, so tests pass thresholds explicitly (keyword-only params, no defaults).
- Bindings under a modal: `App.query_one` resolves against the default screen, so
  refresh loops keep working while a modal is up; guard modal-opening actions with
  `self.screen is not self.default_screen`.
- Untrusted text vs. markup parsing: Textual `Static` widgets parse content markup by
  default, and `DataTable` runs plain-`str` cells through rich's `Text.from_markup`.
  Any pane that displays LLM prompts, token streams, tool summaries, or error messages
  will eventually receive text like `[system] ... key=value ...` or `path=[/x/y]` and
  raise `MarkupError` ("Expected markup value" / "closing tag ... doesn't match") —
  which, inside a refresh loop, spams the feed with a worker error every cycle.
  Construct such Statics with `markup=False`; `rich.markup.escape()` strings headed
  for DataTable cells. Red test: feed the widget real hostile text (see the
  `_HOSTILE` prompt in `tests/tui/test_engine_room.py`) and assert no
  "telemetry … error" lines land in `app.messages`.

## tui_kit test-writing conventions

Where a new tui_kit rendering test belongs depends on what it's exercising:

- **Pure logic/state transitions** — a new bus-item variant, a new `LiveRunState`
  field, a formatting helper like `vitals_line`/`strip_line` — go in
  `test_run_model.py`, or in `test_contracts.py` if it's about the shape of a
  contract event or the `AgentTheme` protocol itself. Neither file boots a Textual
  runtime; both run in milliseconds and should be preferred whenever the behavior can
  be expressed as function-in/dataclass-out.
- **Actual widget mounting or DOM queries** — anything that needs Textual to compose,
  mount, and lay out a widget (`ActivityStrip`, `EngineRoom`, `LiveStreamPanel`, or a
  new one) — goes in `test_widgets.py`, using the minimal `App`/`ComposeResult`
  harness pattern already there (a small `_XHarness(App)` per widget under test,
  driven by `async with app.run_test() as pilot`, asserting on rendered content after
  `await pilot.pause()`).
- **Property-based invariants** — formatting behavior that should hold across
  arbitrary agent names or rosters (glyph/summary rendering, cast ordering) — go in
  `test_roster.py` via Hypothesis `@given`, alongside the example-based cases for
  specific states (idle/running/paused/errored).
- `_FakeTheme` (a small class implementing `glyph`/`label`/`style`/`verb`) is
  currently duplicated per file rather than pulled into a shared fixture — each file
  defines the minimal shape it needs to satisfy the `AgentTheme` protocol. Follow that
  pattern for now rather than introducing a shared conftest fixture unless the
  duplication starts drifting out of sync.
