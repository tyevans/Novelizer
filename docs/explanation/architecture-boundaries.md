# Architecture Boundaries

This document explains the package-level import boundaries enforced across
the repository's four root packages — `substrate`, `novelizer`,
`research_domain`, and `tui_kit` — and why those boundaries are drawn where
they are.

It is an explanation document, not a how-to: it does not walk through
running a command, it walks through the reasoning behind the rules so that
when you hit a boundary violation, or need to decide where a new module
belongs, you understand the design intent rather than just the mechanical
check.

The rules themselves are not restated informally here and then left to
drift — they live in one place, machine-checked, in `pyproject.toml`:

```toml
[tool.importlinter]
root_packages = ["substrate", "novelizer", "research_domain", "tui_kit"]

[[tool.importlinter.contracts]]
name = "substrate package boundary"
type = "forbidden"
source_modules = ["novelizer", "research_domain"]
allow_indirect_imports = "true"
forbidden_modules = [
    "substrate.agent_registry",
    "substrate.event_registry",
    "substrate.policy",
    "substrate.postgres",
    "substrate.projection",
    "substrate.runtime",
]

[[tool.importlinter.contracts]]
name = "tui_kit independence"
type = "forbidden"
source_modules = ["tui_kit"]
forbidden_modules = ["novelizer", "substrate", "research_domain"]
```

Treat `pyproject.toml` as the source of truth. If anything below and the
TOML disagree, the TOML wins and this document is stale — see "Related
documents" for the design specs and the enforcement config to check against.

## Why enforce boundaries with import-linter instead of convention

A rule like "`novelizer` and `research_domain` may only import the
`substrate` top-level package, never its submodules" is easy to write in a
docstring or a code-review checklist. It is just as easy to violate by
accident: nothing about `from substrate.postgres import PostgresEventStore`
looks wrong at the call site, an editor's auto-import will happily suggest
the submodule path, and a reviewer skimming a large diff has no reliable way
to notice that one new `import substrate.runtime` line reaches past the
package's intended surface. Convention only holds as long as every person
touching the codebase remembers it and every review catches every
regression — and that record degrades the moment a new contributor, a
different agent, or a late-night hotfix skips the checklist.

[import-linter](https://import-linter.readthedocs.io/) turns that checklist
into a `forbidden`-type contract that is checked mechanically. In this
repo the check is wired into the test suite itself, not left as a separate
CI step someone has to remember to add: `tests/substrate/test_import_boundary.py`
shells out to the `lint-imports` CLI via `subprocess` and asserts a zero
exit code, so a boundary violation shows up as an ordinary failing pytest
test, in the same place and the same run as every other correctness check.
That means:

- The rule is enforced on every test run, not just when someone happens to
  read this doc or the design spec.
- A violation fails loudly and immediately, with import-linter's own error
  message naming the offending import chain — no need for a reviewer to
  reconstruct the reasoning by hand.
- The rule is expressed once, as data (`root_packages` and the two
  `[[tool.importlinter.contracts]]` tables in `pyproject.toml`), instead of
  being duplicated across docstrings, review checklists, and tribal
  knowledge that can drift out of sync with each other.
- Adding or tightening a boundary is a diff to `pyproject.toml`, reviewable
  like any other code change, rather than an announcement that has to
  propagate through everyone's memory.

This is the same motivation behind the repo's broader commitment to
event sourcing and DDD with enforced module boundaries (see the
"Engineering principles" memory): bounded contexts are only meaningfully
separate if something other than good intentions keeps them that way.

## The four root packages: substrate, novelizer, research_domain, tui_kit

`root_packages` in `pyproject.toml` names exactly the top-level directories
import-linter treats as first-class parties to these boundary rules:
`substrate`, `novelizer`, `research_domain`, and `tui_kit`. Each has a
distinct job:

- **`substrate`** — domain-neutral event-sourcing primitives, proven across
  two independent domains (see `substrate/README.md`). It provides the
  event type registry and gating (`substrate/event_registry.py`),
  projections (`substrate/projection.py`), the agent registry
  (`substrate/agent_registry.py`), Postgres-backed stores
  (`substrate/postgres/`), tiering/gating policy (`substrate/policy.py`),
  and the runtime wiring that assembles them (`substrate/runtime.py`).
  Nothing in `substrate` knows anything about fiction, novels, or
  research — it is the shared kernel underneath both.
- **`novelizer`** — the fiction-writing domain: a story creation tool built
  on knowledge graphs and event chronologies (per its package docstring).
  It has its own agents, brain, canon, chat, director, export, muse,
  research, and scheduler modules, and its own `runtime.py` that wires the
  domain's event types and agents onto `substrate`'s primitives.
- **`research_domain`** — a second, independent domain built on the same
  substrate, deliberately kept synthetic (per `substrate/README.md`'s
  description) to prove that `substrate` generalizes rather than being
  novelizer-shaped in disguise. It has its own `events.py`, `event_types.py`,
  `projections.py`, `roles.py`, `cli.py`, and `runtime.py` — the same shape
  of files `novelizer` has, built against the same substrate surface.
- **`tui_kit`** — a standalone terminal-UI toolkit: `contracts.py` defines
  the event/state contracts its `widgets/` render against, and
  `run_model.py` drives them. It knows about UI contracts and widgets, not
  about events, agents, or any specific domain.

The pairing is deliberate: `novelizer` and `research_domain` are two
*consumers* of `substrate`, built to the same rules, so that a boundary
violation in one is exactly as real a problem as in the other — this doc's
Rule 1 applies to both source packages identically. `tui_kit` sits outside
that relationship entirely; it is a presentation-layer toolkit that neither
depends on nor is depended on by the event-sourcing side of the codebase,
which is what Rule 2 enforces.
