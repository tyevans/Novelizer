# M3 — Declarative Per-Event-Type Autonomy Dial: Pluggability Proof

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prove that a second, independent domain can declare its own event-type gating tiers and get correct `is_gated` behavior without modifying any file under `substrate/` — the actual claim M3 exists to make.

**Architecture:** M1 (Task 2) already did the substantive work the spec's M3 section calls for: it converted fiction's four hardcoded `EventType` sets in `novelizer/canon/policy.py` into data — `EventTypeSpec` entries registered on an `EventTypeRegistry`, gated through the domain-neutral `substrate.policy.is_gated(event_name, registry, tier_order, current_tier_index)`. That already **is** "migrate fiction's own policy.py sets onto this scheme first — dogfooding before a second domain is asked to rely on it," verbatim from the spec. Re-reading M1's diff (commits `409a2de..e296ee3`) confirms `substrate/policy.py` takes the registry and tier order as parameters — it has no fiction-specific code at all.

What M3 has not yet done is *prove* the pluggability claim with a second domain — every registry built and tested so far is fiction's. This milestone is one task: build a synthetic second domain's registry (using a research-flavored event vocabulary as a stand-in, since M4 is the real research domain, but M3 only needs *a* second domain, not *the* second domain) entirely in a test file, register it, and confirm gating behaves correctly — all without touching `substrate/event_registry.py` or `substrate/policy.py`.

Per YAGNI, no new substrate code is written this milestone if the pluggability proof passes on the first try — that would mean M1 already delivered M3's mechanism in full, and this milestone's job is verification, not construction. If the proof reveals a real gap (e.g., some fiction-specific assumption leaked into `substrate/policy.py` that M0/M1 missed), fixing it becomes Task 2, added at that point — not speculated here in advance.

**Tech Stack:** Python 3, pytest — no new dependencies.

## Global Constraints

- Task 1 must not modify any file under `substrate/` — if it needs to, that's a finding to report, not silently work around, since it would mean the pluggability claim doesn't actually hold.
- The synthetic second domain used for the proof must have a materially different tier structure than fiction's (not just a renamed copy of `full_auto/gated_retcons/gated_canon/gated_all`) — otherwise the proof doesn't actually exercise pluggability, just repetition.

---

### Task 1: Second-domain pluggability proof

**Files:**
- Test: `tests/substrate/test_second_domain_pluggability.py`

**Interfaces:**
- Consumes: `substrate.event_registry.EventTypeRegistry`, `EventTypeSpec`, `GatingTier` and `substrate.policy.is_gated` — all from M1, unmodified.
- Produces: nothing new in `substrate/` — this is a proof test only.

- [ ] **Step 1: Write the test, using a two-tier research-flavored vocabulary (deliberately different shape than fiction's four-tier scheme)**

```python
# tests/substrate/test_second_domain_pluggability.py
"""Proves substrate.policy/event_registry are domain-neutral: a second,
independent domain can declare its own event types and gating tiers using
only the public substrate API, with zero changes to substrate/ itself."""
from substrate.event_registry import EventTypeRegistry, EventTypeSpec, GatingTier
from substrate.policy import is_gated

# A synthetic research domain's tier order -- two tiers, not fiction's four,
# to prove the mechanism isn't secretly assuming a fixed tier count/shape.
RESEARCH_TIER_ORDER = ["auto", "reviewed"]


def _research_registry() -> EventTypeRegistry:
    registry = EventTypeRegistry()
    registry.register(EventTypeSpec(name="source.corroborated", gating_tier=GatingTier.never))
    registry.register(EventTypeSpec(name="claim.refuted", gating_tier=GatingTier.always))
    registry.register(
        EventTypeSpec(name="claim.proposed", gating_tier=GatingTier.tiered, tier_level="auto")
    )
    registry.register(
        EventTypeSpec(name="claim.retracted", gating_tier=GatingTier.tiered, tier_level="reviewed")
    )
    return registry


def test_never_gated_research_event_is_never_gated():
    registry = _research_registry()
    assert is_gated("source.corroborated", registry, RESEARCH_TIER_ORDER, current_tier_index=0) is False
    assert is_gated("source.corroborated", registry, RESEARCH_TIER_ORDER, current_tier_index=1) is False


def test_always_gated_research_event_is_always_gated():
    registry = _research_registry()
    assert is_gated("claim.refuted", registry, RESEARCH_TIER_ORDER, current_tier_index=0) is True
    assert is_gated("claim.refuted", registry, RESEARCH_TIER_ORDER, current_tier_index=1) is True


def test_tiered_research_event_gates_at_its_own_tier_not_before():
    registry = _research_registry()
    # "auto" is tier index 0 -- gated as soon as current_tier_index reaches 0
    assert is_gated("claim.proposed", registry, RESEARCH_TIER_ORDER, current_tier_index=0) is True
    # "reviewed" is tier index 1 -- NOT gated while current_tier_index is 0
    assert is_gated("claim.retracted", registry, RESEARCH_TIER_ORDER, current_tier_index=0) is False
    assert is_gated("claim.retracted", registry, RESEARCH_TIER_ORDER, current_tier_index=1) is True


def test_research_and_fiction_registries_are_fully_independent():
    """The same substrate.policy.is_gated call, handed two unrelated
    registries with two unrelated tier vocabularies, must not leak state
    between them -- proves EventTypeRegistry instances don't share module-
    level mutable state."""
    research = _research_registry()
    fiction = EventTypeRegistry()
    fiction.register(EventTypeSpec(name="claim.proposed", gating_tier=GatingTier.never))
    # Same event NAME, opposite gating tier, in a totally separate registry.
    assert is_gated("claim.proposed", research, RESEARCH_TIER_ORDER, current_tier_index=0) is True
    assert is_gated("claim.proposed", fiction, RESEARCH_TIER_ORDER, current_tier_index=0) is False
```

- [ ] **Step 2: Run it**

Run: `.venv/bin/pytest tests/substrate/test_second_domain_pluggability.py -v`
Expected: PASS (4 passed), with zero modifications to any file under
`substrate/`. If any test fails, that is a real finding about substrate's
actual pluggability (not a bug in this test) — read the failure, determine
what assumption in `substrate/policy.py` or `event_registry.py` broke it,
and report back with the specific gap rather than patching the test to
pass around it.

- [ ] **Step 3: Confirm no substrate/ files changed**

Run: `git status --short substrate/`
Expected: no output (clean) — this test file's existence is the only diff.

- [ ] **Step 4: Commit**

```bash
git add tests/substrate/test_second_domain_pluggability.py
git commit -m "test(substrate): prove second-domain pluggability of the declarative gating dial"
```

---

### Task 2: Record M3 completion status

**Files:**
- Modify: `docs/superpowers/specs/2026-07-20-cross-domain-substrate-design.md` (append an "M3 status" note under the M3 section only)

- [ ] **Step 1: Locate the `### M3` section** (find its current text first, don't assume a line number) and append immediately after its existing paragraph:

```markdown

**M3 status (2026-07-21): done — mechanism delivered in M1, pluggability
proven here.** M1 (commits `409a2de..e296ee3`) already converted fiction's
four hardcoded gating sets in `canon/policy.py` into `EventTypeSpec` entries
on an `EventTypeRegistry`, gated through the domain-neutral
`substrate.policy.is_gated(event_name, registry, tier_order,
current_tier_index)` — that already is the "migrate fiction's own
policy.py sets onto this scheme first" dogfooding step this milestone's
spec text calls for. This milestone's own contribution is
`tests/substrate/test_second_domain_pluggability.py`: a synthetic research-
flavored domain, with a two-tier structure deliberately shaped differently
than fiction's four-tier scheme, registers its own event types and gates
correctly through the same `is_gated` call — with zero changes to any file
under `substrate/`. The tier model itself
(`full_auto`/`gated_retcons`/`gated_canon`/`gated_all` + always/never
overrides) was correctly left unchanged, per this milestone's own
non-goals.
```

- [ ] **Step 2: Commit**

```bash
git add docs/superpowers/specs/2026-07-20-cross-domain-substrate-design.md
git commit -m "docs(m3): record M3 completion status"
```
