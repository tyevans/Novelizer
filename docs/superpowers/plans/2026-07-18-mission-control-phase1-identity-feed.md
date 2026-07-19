# Mission Control Phase 1 — Identity & Feed Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **NEVER create a `.env` file at any point in this plan.** Any scratch/temp file goes in
> the job's tmp dir, never the repo. No live-LLM tests are involved in this plan — every
> test here is pure-function or pilot-harness with stub runners.

**Goal:** Give the feed a visual identity — one agent glyph/color source of truth (`identity.py`), a pure `render_event(ev) -> rich.text.Text` with three line classes (canon / remark / alarm), source badges, 2-line clamping, chapter rules, an empty-log welcome block, and `border_title` labels on every existing pane.

**Architecture:** All new rendering is pure functions in `novelizer/tui/widgets/feed_model.py` (same pattern as `proposals_model.py` / `browser_model.py`) plus a tiny identity registry in `novelizer/tui/identity.py`; `novelizer/tui/app.py` only wires them (feed loop writes `Text` objects to the RichLog and appends `text.plain` to `app.messages`). No new events, projections, or read-model changes; widgets stay pure readers of `ReadStore`/`EventStore`.

**Tech Stack:** Python 3.13, uv, Textual 5.3.0 (`RichLog`, `border_title`), `rich.text.Text`, pytest + pytest-asyncio pilot harness, Hypothesis for property tests.

## Global Constraints

- **Identity table is verbatim from the spec** (`docs/superpowers/specs/2026-07-18-mission-control-design-pass-design.md`, "The agent identity system") — glyphs exactly: Author ✎, Editor §, World Architect ⌂, Character Keeper ♥, Continuity Checker ⚖, Retconner ↺, Structure Analyst ∿, Director ★, System ·.
- **Colors** are concrete Rich style names chosen once in `identity.py` (spec names → Rich): amber→`gold3`, violet→`medium_purple`, teal→`dark_cyan`, rose→`hot_pink3`, steel blue→`steel_blue`, orange→`dark_orange`, green→`green3`, white/bold→`bold`, dim→`dim`. All work on dark and light terminals; nothing else in the codebase defines agent colors.
- **`app.messages` stays `list[str]`** — every feed write appends the plain-text rendering (`Text.plain`); existing smoke/resilience/settings-watch assertions stay string-based and must keep passing.
- **`RichLog` keeps `markup=False`.** We write `rich.text.Text` objects, which carry their own styles regardless of `markup` (markup only affects `str` writes). Keeping it False means raw-string writes (`» command result`, settings lines) can never crash or mis-parse on `[` characters (e.g. literal `[source: …]` text).
- **Badge parsing round-trips exactly the four existing constants**: `VOICE_SOURCE_TAG` (`novelizer/agents/editor.py`) → `[drift]`, `LEAK_SOURCE_TAG` (`novelizer/brain/leaks.py`) → `[leak]`, `PARADOX_SOURCE_TAG` (`novelizer/brain/paradoxes.py`) → `[paradox]`, `MINED_SOURCE_TAG` (`novelizer/brain/mining.py`) → `[mined]`. Import the constants — never re-type the tag strings.
- **`roster_summary` (`novelizer/tui/widgets/roster.py`) is NOT touched in Phase 1.** Its tests assert raw agent names (`● author`, `⚠ continuity_checker: boom`); identity labels would break that contract. Phase 3 restyles the roster.
- **Clamping invariant (property-tested):** clamped payload text never exceeds 2 lines of 76 chars each.
- **Markdown `**x**` is rendered as bold, never shown raw.**
- **No new event types, projections, or read-model changes.** Chapter numbers are a running count kept in feed-loop state, not stored anywhere.
- **`format_event(ev) -> str` survives** as a thin `render_event(ev).plain` wrapper so `tests/tui/test_app.py` keeps passing unchanged.
- Textual `>=5.3.0` already pinned; `border_title` has existed since 0.24 — no dependency changes.
- Full suite must pass with `uv run pytest -q`, zero warnings, before the final commit.

---

### Task 1: The agent identity registry (`novelizer/tui/identity.py`)

**Files:**
- Create: `novelizer/tui/identity.py`
- Test: `tests/tui/test_identity.py` (new)

**Interfaces:**
- Consumes: nothing (leaf module; stdlib + dataclasses only).
- Produces: `AgentIdentity` (frozen dataclass: `key: str, label: str, glyph: str, fallback: str, style: str`), `IDENTITIES: dict[str, AgentIdentity]`, `SPEAKER_WIDTH: int = 12`, `identity_for(agent_name: str) -> AgentIdentity`. Tasks 4–5 depend on exactly these names.

The identity table, copied verbatim from the spec — implementers must not guess:

| Agent | Glyph | Color (theme variable) |
|---|---|---|
| Author | ✎ | amber |
| Editor | § | violet |
| World Architect | ⌂ | teal |
| Character Keeper | ♥ | rose |
| Continuity Checker | ⚖ | steel blue |
| Retconner | ↺ | orange |
| Structure Analyst | ∿ | green |
| Director (human) | ★ | white/bold |
| System | · | dim |

Labels reuse the exact short names the feed already uses (see `_AGENT_LABELS` in `novelizer/tui/app.py:29-36`, asserted by `tests/tui/test_app.py::test_format_agent_remarked_labels_each_agent_distinctly`): `Author, Editor, Architect, Keeper, Continuity, Retconner, Analyst`, plus `Director` and `System`.

- [ ] **Step 1: Write the failing test**

```python
# tests/tui/test_identity.py
from novelizer.tui.identity import IDENTITIES, SPEAKER_WIDTH, identity_for


def test_all_seven_agents_plus_director_and_system_present():
    assert set(IDENTITIES) == {
        "author", "editor", "world_architect", "character_keeper",
        "continuity_checker", "retconner", "structure_analyst",
        "director", "system",
    }


def test_glyphs_match_spec_table_verbatim():
    expected = {
        "author": "✎", "editor": "§", "world_architect": "⌂",
        "character_keeper": "♥", "continuity_checker": "⚖",
        "retconner": "↺", "structure_analyst": "∿",
        "director": "★", "system": "·",
    }
    assert {k: v.glyph for k, v in IDENTITIES.items()} == expected


def test_labels_keep_existing_feed_names():
    expected = {
        "author": "Author", "editor": "Editor", "world_architect": "Architect",
        "character_keeper": "Keeper", "continuity_checker": "Continuity",
        "retconner": "Retconner", "structure_analyst": "Analyst",
        "director": "Director", "system": "System",
    }
    assert {k: v.label for k, v in IDENTITIES.items()} == expected


def test_agent_colors_are_unique_and_director_system_are_weight_only():
    agent_styles = [v.style for k, v in IDENTITIES.items() if k not in ("director", "system")]
    assert len(agent_styles) == len(set(agent_styles))
    assert IDENTITIES["director"].style == "bold"
    assert IDENTITIES["system"].style == "dim"


def test_styles_are_valid_rich_styles():
    from rich.style import Style
    for ident in IDENTITIES.values():
        Style.parse(ident.style)  # raises on an invalid style string


def test_identity_for_known_agent_returns_registry_entry():
    assert identity_for("author") is IDENTITIES["author"]


def test_identity_for_unknown_agent_falls_back_to_dim_title_case():
    unknown = identity_for("mystery_agent")
    assert unknown.label == "Mystery Agent"
    assert unknown.glyph == "·"
    assert unknown.style == "dim"


def test_speaker_width_fits_every_glyph_label_pair():
    for ident in IDENTITIES.values():
        assert len(f"{ident.glyph} {ident.label}") <= SPEAKER_WIDTH


def test_every_glyph_is_single_cell_with_single_ascii_fallback():
    for ident in IDENTITIES.values():
        assert len(ident.glyph) == 1
        assert len(ident.fallback) == 1 and ident.fallback.isascii()
```

- [ ] **Step 2: Run test to verify it fails**

```
uv run pytest tests/tui/test_identity.py -v
```
Expected: `ModuleNotFoundError: No module named 'novelizer.tui.identity'` (collection error).

- [ ] **Step 3: Write minimal implementation**

```python
# novelizer/tui/identity.py
"""Single source of truth for agent identity: glyph, label, color.

The spec's identity table (docs/superpowers/specs/
2026-07-18-mission-control-design-pass-design.md) rendered as data. Every
place an agent appears — feed speaker column, and (Phase 3) roster and room
view — reads from here. Spec color names map to Rich styles that read well on
both dark and light terminals:

    amber -> gold3, violet -> medium_purple, teal -> dark_cyan,
    rose -> hot_pink3, steel blue -> steel_blue, orange -> dark_orange,
    green -> green3, white/bold -> bold, dim -> dim
"""
from __future__ import annotations
from dataclasses import dataclass


@dataclass(frozen=True)
class AgentIdentity:
    key: str        # canonical agent_name, e.g. "character_keeper"
    label: str      # short feed label, e.g. "Keeper"
    glyph: str      # single-cell glyph from the spec table
    fallback: str   # single ASCII letter if the terminal lacks the glyph
    style: str      # Rich style string — defined once, here only


IDENTITIES: dict[str, AgentIdentity] = {
    "author": AgentIdentity("author", "Author", "✎", "A", "gold3"),
    "editor": AgentIdentity("editor", "Editor", "§", "E", "medium_purple"),
    "world_architect": AgentIdentity("world_architect", "Architect", "⌂", "W", "dark_cyan"),
    "character_keeper": AgentIdentity("character_keeper", "Keeper", "♥", "K", "hot_pink3"),
    "continuity_checker": AgentIdentity("continuity_checker", "Continuity", "⚖", "C", "steel_blue"),
    "retconner": AgentIdentity("retconner", "Retconner", "↺", "R", "dark_orange"),
    "structure_analyst": AgentIdentity("structure_analyst", "Analyst", "∿", "S", "green3"),
    "director": AgentIdentity("director", "Director", "★", "D", "bold"),
    "system": AgentIdentity("system", "System", "·", "-", "dim"),
}

# glyph + space + longest label ("Continuity", 10 cells) = 12; the feed's
# fixed speaker column pads to this so lines scan like a screenplay.
SPEAKER_WIDTH = 12


def identity_for(agent_name: str) -> AgentIdentity:
    """Registry lookup with a dim, title-cased fallback for unknown names
    (preserves the existing 'Mystery Agent' behavior of _agent_label)."""
    ident = IDENTITIES.get(agent_name)
    if ident is not None:
        return ident
    label = agent_name.replace("_", " ").title() or "System"
    return AgentIdentity(agent_name, label, "·", "-", "dim")
```

- [ ] **Step 4: Run test to verify it passes**

```
uv run pytest tests/tui/test_identity.py -v
```
Expected: 9 passed.

- [ ] **Step 5: Commit**

```
git add novelizer/tui/identity.py tests/tui/test_identity.py
git commit -m "feat: agent identity registry — one glyph/color/label source of truth"
```

---

### Task 2: Source-badge parser (`feed_model.py`, part 1)

**Files:**
- Create: `novelizer/tui/widgets/feed_model.py`
- Test: `tests/tui/test_feed_model.py` (new)

**Interfaces:**
- Consumes: `VOICE_SOURCE_TAG` from `novelizer.agents.editor`, `LEAK_SOURCE_TAG` from `novelizer.brain.leaks`, `PARADOX_SOURCE_TAG` from `novelizer.brain.paradoxes`, `MINED_SOURCE_TAG` from `novelizer.brain.mining` (all light imports — verified none pulls an LLM client at import time).
- Produces: `SOURCE_BADGES: dict[str, str]`, `parse_source_badge(description: str) -> tuple[str | None, str]`. Task 4's alarm rendering depends on exactly these.

- [ ] **Step 1: Write the failing test**

```python
# tests/tui/test_feed_model.py
from hypothesis import given, strategies as st

from novelizer.agents.editor import VOICE_SOURCE_TAG
from novelizer.brain.leaks import LEAK_SOURCE_TAG
from novelizer.brain.mining import MINED_SOURCE_TAG
from novelizer.brain.paradoxes import PARADOX_SOURCE_TAG
from novelizer.tui.widgets.feed_model import SOURCE_BADGES, parse_source_badge

_ALL_TAGS = [VOICE_SOURCE_TAG, LEAK_SOURCE_TAG, PARADOX_SOURCE_TAG, MINED_SOURCE_TAG]


def test_every_source_tag_constant_has_a_badge():
    assert set(SOURCE_BADGES) == set(_ALL_TAGS)


def test_badges_are_the_spec_short_forms():
    assert SOURCE_BADGES[VOICE_SOURCE_TAG] == "[drift]"
    assert SOURCE_BADGES[LEAK_SOURCE_TAG] == "[leak]"
    assert SOURCE_BADGES[PARADOX_SOURCE_TAG] == "[paradox]"
    assert SOURCE_BADGES[MINED_SOURCE_TAG] == "[mined]"


@given(tag=st.sampled_from(_ALL_TAGS), rest=st.text(max_size=200))
def test_badge_parser_round_trips_every_source_tag(tag, rest):
    badge, remainder = parse_source_badge(f"{tag} {rest}")
    assert badge == SOURCE_BADGES[tag]
    assert remainder == rest.lstrip()


def test_untagged_description_passes_through_unbadged():
    assert parse_source_badge("scar mismatch") == (None, "scar mismatch")


def test_unknown_source_tag_is_left_intact_not_badged():
    desc = "[source: gremlin] something odd"
    assert parse_source_badge(desc) == (None, desc)
```

- [ ] **Step 2: Run test to verify it fails**

```
uv run pytest tests/tui/test_feed_model.py -v
```
Expected: `ModuleNotFoundError: No module named 'novelizer.tui.widgets.feed_model'` (collection error).

- [ ] **Step 3: Write minimal implementation**

```python
# novelizer/tui/widgets/feed_model.py
"""Pure feed rendering: StoredEvent -> rich.text.Text.

Same seam and testability as the other *_model.py modules — no Textual
imports, no I/O, unit-testable without a terminal.
"""
from __future__ import annotations

from novelizer.agents.editor import VOICE_SOURCE_TAG
from novelizer.brain.leaks import LEAK_SOURCE_TAG
from novelizer.brain.mining import MINED_SOURCE_TAG
from novelizer.brain.paradoxes import PARADOX_SOURCE_TAG

# The four alarm sources map to short badges instead of printing
# "[source: voice_drift]" raw in the feed. Keys are the imported constants —
# if a tag string ever changes at its source, the mapping follows.
SOURCE_BADGES: dict[str, str] = {
    VOICE_SOURCE_TAG: "[drift]",
    LEAK_SOURCE_TAG: "[leak]",
    PARADOX_SOURCE_TAG: "[paradox]",
    MINED_SOURCE_TAG: "[mined]",
}


def parse_source_badge(description: str) -> tuple[str | None, str]:
    """Split a retcon description into (badge, remaining text).

    A description prefixed by a known *_SOURCE_TAG yields its short badge and
    the text with the tag stripped; anything else yields (None, description)
    untouched — unknown tags stay visible rather than being silently eaten.
    """
    for tag, badge in SOURCE_BADGES.items():
        if description.startswith(tag):
            return badge, description[len(tag):].lstrip()
    return None, description
```

- [ ] **Step 4: Run test to verify it passes**

```
uv run pytest tests/tui/test_feed_model.py -v
```
Expected: 5 passed (the Hypothesis test counts as one).

- [ ] **Step 5: Commit**

```
git add novelizer/tui/widgets/feed_model.py tests/tui/test_feed_model.py
git commit -m "feat: source-badge parser — [drift]/[leak]/[paradox]/[mined] from *_SOURCE_TAG"
```

---

### Task 3: Clamping and inline markdown (`feed_model.py`, part 2)

**Files:**
- Modify: `novelizer/tui/widgets/feed_model.py` (append to file from Task 2)
- Test: `tests/tui/test_feed_model.py` (append)

**Interfaces:**
- Consumes: nothing new (stdlib `re`, `textwrap`; `rich.text.Text`).
- Produces: `CLAMP_WIDTH: int = 76`, `CLAMP_LINES: int = 2`, `clamp_text(s: str, width: int = CLAMP_WIDTH, max_lines: int = CLAMP_LINES) -> tuple[str, bool]`, `md_inline(s: str) -> Text`. Task 4 depends on exactly these.

- [ ] **Step 1: Write the failing test** (append to `tests/tui/test_feed_model.py`)

```python
from novelizer.tui.widgets.feed_model import CLAMP_LINES, CLAMP_WIDTH, clamp_text, md_inline


def test_clamp_short_text_is_unchanged_and_not_truncated():
    assert clamp_text("a quiet line") == ("a quiet line", False)


def test_clamp_empty_text():
    assert clamp_text("") == ("", False)


def test_clamp_collapses_newlines_and_truncates_long_payloads():
    long = "line one\nline two\nline three\n" + "critique " * 200
    clamped, truncated = clamp_text(long)
    assert truncated is True
    lines = clamped.splitlines()
    assert len(lines) == CLAMP_LINES
    assert all(len(line) <= CLAMP_WIDTH for line in lines)
    assert clamped.startswith("line one line two line three")


@given(st.text(max_size=2000))
def test_clamp_never_exceeds_two_lines_of_width(s):
    clamped, _ = clamp_text(s)
    lines = clamped.splitlines()
    assert len(lines) <= CLAMP_LINES
    assert all(len(line) <= CLAMP_WIDTH for line in lines)


def test_md_inline_renders_bold_and_never_shows_raw_stars():
    text = md_inline("the **closing image** lands")
    assert text.plain == "the closing image lands"
    assert "**" not in text.plain
    bold_spans = [sp for sp in text.spans if "bold" in str(sp.style)]
    assert len(bold_spans) == 1
    assert text.plain[bold_spans[0].start:bold_spans[0].end] == "closing image"


def test_md_inline_leaves_a_lone_star_pair_literal():
    # only *paired* ** markers are markdown; a single ** is not eaten
    assert md_inline("2 ** 3 is eight").plain == "2 ** 3 is eight"
```

- [ ] **Step 2: Run test to verify it fails**

```
uv run pytest tests/tui/test_feed_model.py -v
```
Expected: `ImportError: cannot import name 'CLAMP_LINES' from 'novelizer.tui.widgets.feed_model'` (collection error); Task 2's tests still pass when run alone.

- [ ] **Step 3: Write minimal implementation** (append to `novelizer/tui/widgets/feed_model.py`; add `import re`, `import textwrap`, and `from rich.text import Text` to the imports block)

```python
import re
import textwrap

from rich.text import Text

CLAMP_WIDTH = 76
CLAMP_LINES = 2

_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")


def clamp_text(s: str, width: int = CLAMP_WIDTH, max_lines: int = CLAMP_LINES) -> tuple[str, bool]:
    """Collapse whitespace and clamp to at most max_lines lines of at most
    width chars. Returns (clamped, truncated). The feed is a pulse, not a
    document — the full text is always one selection away in the detail pane.
    """
    collapsed = " ".join(s.split())
    if not collapsed:
        return "", False
    lines = textwrap.wrap(collapsed, width=width, break_long_words=True, break_on_hyphens=False)
    if len(lines) <= max_lines:
        return "\n".join(lines), False
    return "\n".join(lines[:max_lines]), True


def md_inline(s: str) -> Text:
    """Render inline markdown bold (**x** -> bold span); never show ** raw.

    Only balanced ** pairs are treated as markup; anything unpaired stays
    literal, so stray asterisks in agent prose survive.
    """
    text = Text()
    pos = 0
    for m in _BOLD_RE.finditer(s):
        text.append(s[pos:m.start()])
        text.append(m.group(1), style="bold")
        pos = m.end()
    text.append(s[pos:])
    return text
```

- [ ] **Step 4: Run test to verify it passes**

```
uv run pytest tests/tui/test_feed_model.py -v
```
Expected: 11 passed.

- [ ] **Step 5: Commit**

```
git add novelizer/tui/widgets/feed_model.py tests/tui/test_feed_model.py
git commit -m "feat: feed clamp (2 lines x 76 cells, property-tested) + inline markdown bold"
```

---

### Task 4: `render_event` — three line classes, speaker column, chips (`feed_model.py`, part 3)

**Files:**
- Modify: `novelizer/tui/widgets/feed_model.py` (append)
- Modify: `novelizer/tui/app.py` — replace `_LABELS` / `_AGENT_LABELS` / `_agent_label` / `format_event` (lines 20–62) with a thin delegation
- Test: `tests/tui/test_feed_model.py` (append); `tests/tui/test_app.py` must keep passing unchanged

**Interfaces:**
- Consumes: `identity_for`, `SPEAKER_WIDTH` (Task 1); `parse_source_badge` (Task 2); `clamp_text`, `md_inline` (Task 3); `StoredEvent`, `EventType` from `novelizer.canon.events`.
- Produces: `render_event(ev: StoredEvent) -> Text`, `chapter_rule(number: int, title: str) -> Text`, `welcome_lines() -> list[Text]`, `worker_error_line(worker_name: str, error: Exception) -> Text`, `ALARM_STYLE = "bold red"`. Task 5 wires exactly these; `format_event(ev) -> str` in app.py becomes `render_event(ev).plain`.

Design decisions locked here (so no implementer guesses):
- **Speaker mapping** (StoredEvent records no actor, so the speaker is inferred from event type, extending the existing `_LABELS` convention): `chapter.created` → author; `chapter.status_changed` → editor; `world_entry.*` → world_architect; `character.*` → character_keeper; `director_signal.created` → director; `retcon_request.*` → retconner (keeps the existing `"Retcon" in line` test passing — the badge, not the speaker, carries the true alarm source); `annotation.structure_scored` → structure_analyst; everything else → system.
- **Alarm class** = `retcon_request.created` (leaks, paradoxes, voice drift, and mined facts all arrive on this one event type, distinguished by badge) plus worker errors (via `worker_error_line`).
- **Domain chips** on canon lines are derived from the event-type prefix, with readable aliases: `world_entry`→`lore`, `causal_edge`→`cause`, `annotation`→`shape`, `director_signal`→`signal`; all others use the prefix itself (`chapter`, `secret`, `thread`, `theme`, `character`, `proposal`, `autonomy`).

- [ ] **Step 1: Write the failing test** (append to `tests/tui/test_feed_model.py`)

```python
from novelizer.canon.events import EventType, StoredEvent
from novelizer.tui.widgets.feed_model import (
    ALARM_STYLE, chapter_rule, render_event, welcome_lines, worker_error_line,
)


def _ev(event_type, payload, seq=1):
    return StoredEvent(sequence=seq, id=f"e{seq}", event_type=event_type,
                       aggregate_id="agg", payload=payload, created_at="t")


def test_render_chapter_created_has_speaker_column_and_chapter_chip():
    text = render_event(_ev(EventType.CHAPTER_CREATED, {"title": "The Salt Road"}))
    assert text.plain.startswith("✎ Author")
    assert 'drafted "The Salt Road"' in text.plain
    assert text.plain.rstrip().endswith("◆ chapter")


def test_render_world_entry_uses_architect_and_lore_chip():
    text = render_event(_ev(EventType.WORLD_ENTRY_CREATED, {"title": "Brinemarsh"}))
    assert text.plain.startswith("⌂ Architect")
    assert "Brinemarsh" in text.plain
    assert text.plain.rstrip().endswith("◆ lore")


def test_render_chapter_status_changed_speaks_as_editor():
    text = render_event(_ev(EventType.CHAPTER_STATUS_CHANGED,
                            {"title": "One", "editorial_status": "reviewed"}))
    assert text.plain.startswith("§ Editor")
    assert "One" in text.plain


def test_render_unmapped_canon_event_falls_back_to_payload_title_and_domain_chip():
    text = render_event(_ev(EventType.SECRET_CREATED, {"id": "s", "title": "The Heir Lives"}))
    assert text.plain.startswith("· System")
    assert "The Heir Lives" in text.plain
    assert text.plain.rstrip().endswith("◆ secret")


def test_render_remark_is_dim_italic_with_speech_glyph():
    text = render_event(_ev(EventType.AGENT_REMARKED,
                            {"agent_name": "character_keeper",
                             "note": "Elara wouldn't say it that plainly."}))
    assert text.plain.startswith("♥ Keeper")
    assert "💬" in text.plain
    assert "Elara wouldn't say it that plainly." in text.plain
    assert any("italic" in str(span.style) for span in text.spans)


def test_render_remark_unknown_agent_uses_title_case_fallback():
    text = render_event(_ev(EventType.AGENT_REMARKED,
                            {"agent_name": "mystery_agent", "note": "?"}))
    assert "Mystery Agent" in text.plain


def test_render_retcon_created_is_alarm_with_parsed_badge():
    from novelizer.agents.editor import VOICE_SOURCE_TAG
    text = render_event(_ev(EventType.RETCON_REQUEST_CREATED,
                            {"description": f"{VOICE_SOURCE_TAG} clean and neutral violated"}))
    assert text.plain.startswith("↺ Retconner")
    assert "⚠" in text.plain
    assert "[drift]" in text.plain
    assert "[source: voice_drift]" not in text.plain
    assert any(str(span.style) == ALARM_STYLE for span in text.spans)


def test_render_retcon_without_tag_is_alarm_without_badge():
    text = render_event(_ev(EventType.RETCON_REQUEST_CREATED, {"description": "scar mismatch"}))
    assert "scar mismatch" in text.plain and "⚠" in text.plain
    assert "[" not in text.plain


def test_render_strips_markdown_bold_from_payload_text():
    text = render_event(_ev(EventType.AGENT_REMARKED,
                            {"agent_name": "editor", "note": "the **closing image** lands"}))
    assert "**" not in text.plain
    assert "closing image" in text.plain


def test_render_clamps_long_note_to_two_lines_with_dim_continuation():
    text = render_event(_ev(EventType.AGENT_REMARKED,
                            {"agent_name": "editor", "note": "critique " * 100}))
    assert len(text.plain.splitlines()) <= 2
    assert text.plain.rstrip().endswith("…")


def test_chapter_rule_is_a_dim_horizontal_rule():
    text = chapter_rule(4, "The Name in the Wind")
    assert text.plain == "── ch 4 · The Name in the Wind ──"
    assert str(text.style) == "dim"


def test_welcome_lines_are_the_spec_director_voice_verbatim():
    lines = welcome_lines()
    assert [t.plain for t in lines] == [
        "★ The room is assembled: Author, Editor, Architect, Keeper, Continuity, Retconner, Analyst.",
        "★ It's quiet. Give them a world:  :seed a lighthouse keeper who taxes the tide",
    ]
    assert all(str(t.style) == "bold" for t in lines)


def test_worker_error_line_is_a_plain_compatible_alarm():
    text = worker_error_line("feed", RuntimeError("boom"))
    assert text.plain == "⚠ feed error: boom"
    assert str(text.style) == ALARM_STYLE


def test_format_event_is_the_plain_rendering():
    from novelizer.tui.app import format_event
    ev = _ev(EventType.CHAPTER_CREATED, {"title": "One"})
    assert format_event(ev) == render_event(ev).plain
```

- [ ] **Step 2: Run test to verify it fails**

```
uv run pytest tests/tui/test_feed_model.py -v
```
Expected: `ImportError: cannot import name 'ALARM_STYLE' from 'novelizer.tui.widgets.feed_model'` (collection error).

- [ ] **Step 3: Write minimal implementation**

Append to `novelizer/tui/widgets/feed_model.py` (add `from novelizer.canon.events import EventType, StoredEvent` and `from novelizer.tui.identity import SPEAKER_WIDTH, identity_for` to the imports block):

```python
ALARM_STYLE = "bold red"
CHIP_STYLE = "dim"
CONTINUATION = " …"

# StoredEvent records no actor, so the speaker is inferred from event type —
# same convention the old _LABELS dict used, extended. retcon_request.* maps
# to the Retconner (the agent that owns the retcon queue); the *source* of an
# alarm is carried by its badge, not its speaker.
_EVENT_SPEAKERS: dict[str, str] = {
    EventType.CHAPTER_CREATED: "author",
    EventType.CHAPTER_STATUS_CHANGED: "editor",
    EventType.WORLD_ENTRY_CREATED: "world_architect",
    EventType.WORLD_ENTRY_SUPERSEDED: "world_architect",
    EventType.CHARACTER_CREATED: "character_keeper",
    EventType.CHARACTER_UPDATED: "character_keeper",
    EventType.DIRECTOR_SIGNAL_CREATED: "director",
    EventType.RETCON_REQUEST_CREATED: "retconner",
    EventType.RETCON_REQUEST_RESOLVED: "retconner",
    EventType.RETCON_REQUEST_REJECTED: "retconner",
    EventType.ANNOTATION_STRUCTURE_SCORED: "structure_analyst",
}

_ALARM_EVENTS = {EventType.RETCON_REQUEST_CREATED}

# event-type prefix -> readable domain chip; unlisted prefixes read fine as-is
_DOMAIN_CHIPS = {
    "world_entry": "lore",
    "causal_edge": "cause",
    "annotation": "shape",
    "director_signal": "signal",
}

WELCOME_PLAIN = (
    "★ The room is assembled: Author, Editor, Architect, Keeper, Continuity, Retconner, Analyst.",
    "★ It's quiet. Give them a world:  :seed a lighthouse keeper who taxes the tide",
)


def _speaker(agent_name: str) -> Text:
    ident = identity_for(agent_name)
    return Text(f"{ident.glyph} {ident.label}".ljust(SPEAKER_WIDTH), style=ident.style)


def _domain_chip(event_type: str) -> str:
    domain = event_type.split(".", 1)[0]
    return _DOMAIN_CHIPS.get(domain, domain)


def _detail_for(ev: StoredEvent) -> str:
    p = ev.payload
    t = ev.event_type
    if t == EventType.CHAPTER_CREATED:
        return f'drafted "{p.get("title", "")}"'
    if t == EventType.CHAPTER_STATUS_CHANGED:
        return f'reviewed "{p.get("title", "")}" — {p.get("editorial_status", "")}'
    if t == EventType.WORLD_ENTRY_CREATED:
        return f"lore: {p.get('title', '')}"
    if t == EventType.CHARACTER_CREATED:
        return f"new character: {p.get('name', '')}"
    if t == EventType.DIRECTOR_SIGNAL_CREATED:
        return f"signal: {p.get('body', '')}"
    if t == EventType.ANNOTATION_STRUCTURE_SCORED:
        return f"scored — tension {float(p.get('tension', 0.0)):.2f}, {p.get('pacing_label', '')}"
    for key in ("title", "name", "note", "description", "body"):
        val = p.get(key)
        if val:
            return str(val)
    return t


def render_event(ev: StoredEvent) -> Text:
    """One feed line per event: canon (speaker + detail + dim domain chip),
    remark (dim italic 💬), or alarm (bold red ⚠ + source badge)."""
    if ev.event_type == EventType.AGENT_REMARKED:
        note, truncated = clamp_text(ev.payload.get("note", ""))
        line = _speaker(ev.payload.get("agent_name", "system"))
        line.append("💬 ", style="dim italic")
        body = md_inline(f'"{note}"')
        body.stylize("dim italic")
        line.append_text(body)
        if truncated:
            line.append(CONTINUATION, style="dim")
        return line

    speaker = _EVENT_SPEAKERS.get(ev.event_type, "system")
    if ev.event_type in _ALARM_EVENTS:
        badge, rest = parse_source_badge(ev.payload.get("description", ""))
        detail, truncated = clamp_text(f"retcon filed: {rest}")
        line = _speaker(speaker)
        body = md_inline(f"⚠ {detail}")
        body.stylize(ALARM_STYLE)
        line.append_text(body)
        if truncated:
            line.append(CONTINUATION, style="dim")
        if badge:
            line.append(f"  {badge}", style=ALARM_STYLE)
        return line

    detail, truncated = clamp_text(_detail_for(ev))
    line = _speaker(speaker)
    line.append_text(md_inline(detail))
    if truncated:
        line.append(CONTINUATION, style="dim")
    line.append(f"  ◆ {_domain_chip(ev.event_type)}", style=CHIP_STYLE)
    return line


def chapter_rule(number: int, title: str) -> Text:
    """Dim horizontal rule written before each chapter.created line, so the
    feed self-organizes into acts. `number` is the running chapter count —
    tracked by the feed loop, never stored (no new events/projections)."""
    return Text(f"── ch {number} · {title} ──", style="dim")


def welcome_lines() -> list[Text]:
    """Director-voiced two-line welcome for a story with an empty event log."""
    director = identity_for("director")
    return [Text(line, style=director.style) for line in WELCOME_PLAIN]


def worker_error_line(worker_name: str, error: Exception) -> Text:
    """Worker errors are alarms too — same plain text as before (resilience
    tests assert on it), now styled."""
    return Text(f"⚠ {worker_name} error: {error}", style=ALARM_STYLE)
```

In `novelizer/tui/app.py`, delete `_LABELS` (lines 20–27), `_AGENT_LABELS` (lines 29–36), `_agent_label` (lines 39–40), and the body of `format_event` (lines 43–62), replacing them with (keep the existing `from novelizer.canon.events import StoredEvent, EventType` import; add the feed_model import next to the other widget imports):

```python
from novelizer.tui.widgets.feed_model import (
    chapter_rule, render_event, welcome_lines, worker_error_line,
)


def format_event(ev: StoredEvent) -> str:
    """Plain-text rendering of a feed line — the string surface app.messages
    and the existing tests assert on. Styling lives in render_event."""
    return render_event(ev).plain
```

(`chapter_rule` / `welcome_lines` / `worker_error_line` are imported now, wired in Task 5 — if the linter flags them as unused at this step, import only `render_event` here and add the rest in Task 5.)

- [ ] **Step 4: Run test to verify it passes**

```
uv run pytest tests/tui/test_feed_model.py tests/tui/test_app.py tests/tui/test_identity.py -v
```
Expected: all pass — including every pre-existing `tests/tui/test_app.py` test, unchanged ("The Salt Road"/"Author", "Retcon", "Editor", 💬, "Mystery Agent" are all present in the new plain renderings).

- [ ] **Step 5: Commit**

```
git add novelizer/tui/widgets/feed_model.py novelizer/tui/app.py tests/tui/test_feed_model.py
git commit -m "feat: render_event — speaker column, canon/remark/alarm line classes, chips + badges"
```

---

### Task 5: Feed-loop wiring — Text writes, chapter rules, welcome block, alarm-styled worker errors

**Files:**
- Modify: `novelizer/tui/app.py` — `__init__` (lines ~90–94), `_report_worker_error` (lines ~129–136), `_feed_loop` (lines ~154–166)
- Test: `tests/tui/test_feed_wiring.py` (new, pilot harness per `tests/tui/test_app_smoke.py`)

**Interfaces:**
- Consumes: `render_event`, `chapter_rule`, `welcome_lines`, `worker_error_line` (Task 4); `EventStore.events_since(seq)` (existing).
- Produces: runtime behavior only. `app.messages` remains `list[str]` (each entry is the `Text.plain` of what was written). `self._chapter_count: int` is new feed-loop state.

Notes locked here:
- `RichLog` stays `markup=False` (see Global Constraints) — `Text` objects carry styles regardless.
- Chapter numbering: the feed loop replays from sequence 0 on every mount, so a plain running counter is correct and stable across restarts.
- Welcome block: shown only when `events_since(0)` is empty at mount — one probe before the poll loop, so it can never interleave with real events.

- [ ] **Step 1: Write the failing test**

```python
# tests/tui/test_feed_wiring.py
import os
import tempfile
import pytest
from novelizer.settings import EffectiveSettings as Settings
from novelizer.runtime import Runtime
from novelizer.tui.app import NovelizerApp
from novelizer.agents.schemas import (
    WorldEntriesDraft, WorldEntryDraft, KeeperOutput, EditorVerdict,
    ContinuityOutput, RetconAmendments, StructureAnalystOutput,
)
from novelizer.agents.base import ChapterDraft

_AGENTS = ["world_architect", "character_keeper", "author", "editor",
           "continuity_checker", "retconner", "structure_analyst"]


class _R:
    def __init__(self, out): self._out = out
    async def ainvoke(self, inputs): return {"structured_response": self._out}


def _runners():
    return {
        "world_architect": _R(WorldEntriesDraft(entries=[WorldEntryDraft(title="Brinemarsh", body="salt")])),
        "author": _R(ChapterDraft(title="Chapter One", prose="It began.")),
        "character_keeper": _R(KeeperOutput()),
        "editor": _R(EditorVerdict(verdict="approve", notes="ok")),
        "continuity_checker": _R(ContinuityOutput()),
        "retconner": _R(RetconAmendments()),
        "structure_analyst": _R(StructureAnalystOutput()),
    }


async def _quiet_runtime(path):
    settings = Settings(db_path=path, projector_interval=0.1,
                        author_interval=100, default_agent_interval=100, continuity_interval=100)
    rt = Runtime(settings, runners=_runners())
    await rt.start()
    for name in _AGENTS:
        rt.scheduler.pause_agent(name)
    return rt


@pytest.mark.asyncio
async def test_empty_log_shows_director_welcome_block():
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    rt = await _quiet_runtime(path)
    app = NovelizerApp(rt)
    try:
        async with app.run_test() as pilot:
            await pilot.pause(0.5)
            joined = "\n".join(app.messages)
            assert "★ The room is assembled: Author, Editor, Architect, Keeper, Continuity, Retconner, Analyst." in joined
            assert ":seed a lighthouse keeper who taxes the tide" in joined
            assert all(isinstance(m, str) for m in app.messages)
    finally:
        await rt.close(); os.unlink(path)


@pytest.mark.asyncio
async def test_chapter_created_writes_numbered_rule_lines_and_no_welcome():
    from novelizer.canon.events import EventType
    from novelizer.store.models import Chapter

    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    rt = await _quiet_runtime(path)
    await rt.events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
    await rt.events.append(EventType.CHAPTER_CREATED, "c2", Chapter(id="c2", title="Two", prose="p"))
    app = NovelizerApp(rt)
    try:
        async with app.run_test() as pilot:
            await pilot.pause(0.6)
            joined = "\n".join(app.messages)
            assert "── ch 1 · One ──" in joined
            assert "── ch 2 · Two ──" in joined
            assert 'drafted "Two"' in joined       # the event line itself still renders
            assert "★ The room is assembled" not in joined  # log wasn't empty
            assert all(isinstance(m, str) for m in app.messages)
    finally:
        await rt.close(); os.unlink(path)
```

- [ ] **Step 2: Run test to verify it fails**

```
uv run pytest tests/tui/test_feed_wiring.py -v
```
Expected: 2 failed — `AssertionError` on `"★ The room is assembled..."` and on `"── ch 1 · One ──"` (the current loop writes neither).

- [ ] **Step 3: Write minimal implementation**

In `novelizer/tui/app.py` (feed_model names already imported in Task 4):

`__init__` gains the counter:

```python
    def __init__(self, runtime) -> None:
        super().__init__()
        self.runtime = runtime
        self._last_seq = 0
        self._chapter_count = 0
        self.messages: list[str] = []
```

`_report_worker_error` becomes:

```python
    def _report_worker_error(self, worker_name: str, e: Exception) -> None:
        line = worker_error_line(worker_name, e)
        try:
            log = self.query_one("#feed", RichLog)
            log.write(line)
        except Exception:
            pass
        self.messages.append(line.plain)
```

`_feed_loop` becomes:

```python
    async def _feed_loop(self) -> None:
        log = self.query_one("#feed", RichLog)
        try:
            if not await self.runtime.events.events_since(0):
                for line in welcome_lines():
                    log.write(line)
                    self.messages.append(line.plain)
        except Exception as e:
            self._report_worker_error("feed", e)
        while True:
            try:
                events = await self.runtime.events.events_since(self._last_seq)
                for ev in events:
                    if ev.event_type == EventType.CHAPTER_CREATED:
                        self._chapter_count += 1
                        rule = chapter_rule(self._chapter_count, ev.payload.get("title", ""))
                        log.write(rule)
                        self.messages.append(rule.plain)
                    rendered = render_event(ev)
                    log.write(rendered)
                    self.messages.append(rendered.plain)
                    self._last_seq = ev.sequence
            except Exception as e:
                self._report_worker_error("feed", e)
            await asyncio.sleep(0.3)
```

- [ ] **Step 4: Run test to verify it passes**

```
uv run pytest tests/tui/test_feed_wiring.py tests/tui/test_app_smoke.py tests/tui/test_app_resilience.py tests/tui/test_settings_watch.py -v
```
Expected: all pass — smoke still finds `"Live Chapter"` in `app.messages` (now via `render_event(...).plain` and the rule line), resilience still finds `"boom"`/`"scheduler"` (`worker_error_line(...).plain` is byte-identical to the old f-string) and `"a storm is coming"`, settings-watch lines are untouched raw strings.

- [ ] **Step 5: Commit**

```
git add novelizer/tui/app.py tests/tui/test_feed_wiring.py
git commit -m "feat: feed loop writes styled Text — chapter rules, empty-log welcome, alarm worker errors"
```

---

### Task 6: Border titles on every pane

**Files:**
- Modify: `novelizer/tui/app.py` — `compose()` (lines ~96–114)
- Modify: `novelizer/tui/app.tcss` (append two rules)
- Test: `tests/tui/test_app_layout.py` (append one test)

**Interfaces:**
- Consumes: nothing from earlier tasks (independent — reviewer can approve/reject separately).
- Produces: `border_title` set on `#feed` = `THE ROOM`, `#proposals` = `PROPOSALS`, `#thread_board` = `THREADS`, `#story_shape` = `STORY SHAPE`, `#who_knows_what` = `WHO KNOWS WHAT`, `#causeway` = `CAUSEWAY`, `#browser` = `STORY`, `#detail_scroll` = `DETAIL`. (Phase 1 only labels what exists; pane restructuring and the dynamic detail title are Phase 2/3.)

- [ ] **Step 1: Write the failing test** (append to `tests/tui/test_app_layout.py`; it already imports `Settings`, `Runtime`, `NovelizerApp`, `_runners`, `os`, `tempfile`, `pytest`)

```python
@pytest.mark.asyncio
async def test_every_pane_has_its_border_title():
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    settings = Settings(db_path=path, projector_interval=0.1)
    rt = Runtime(settings, runners=_runners())
    await rt.start()
    for name in ["world_architect", "character_keeper", "author", "editor",
                 "continuity_checker", "retconner", "structure_analyst"]:
        rt.scheduler.pause_agent(name)
    app = NovelizerApp(rt)
    try:
        async with app.run_test():
            expected = {
                "#feed": "THE ROOM",
                "#proposals": "PROPOSALS",
                "#thread_board": "THREADS",
                "#story_shape": "STORY SHAPE",
                "#who_knows_what": "WHO KNOWS WHAT",
                "#causeway": "CAUSEWAY",
                "#browser": "STORY",
                "#detail_scroll": "DETAIL",
            }
            for selector, title in expected.items():
                assert str(app.query_one(selector).border_title) == title, selector
    finally:
        await rt.close(); os.unlink(path)
```

- [ ] **Step 2: Run test to verify it fails**

```
uv run pytest tests/tui/test_app_layout.py::test_every_pane_has_its_border_title -v
```
Expected: `AssertionError: #feed` (border_title is `None`, `str(None) == "None" != "THE ROOM"`).

- [ ] **Step 3: Write minimal implementation**

Replace `compose()` in `novelizer/tui/app.py` with:

```python
    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="body"):
            with Vertical(id="left"):
                feed = RichLog(highlight=False, markup=False, id="feed")
                feed.border_title = "THE ROOM"
                yield feed
                proposals = Static("no pending proposals", id="proposals")
                proposals.border_title = "PROPOSALS"
                yield proposals
                thread_board = ThreadBoard("no threads yet", id="thread_board")
                thread_board.border_title = "THREADS"
                yield thread_board
                story_shape = StoryShape("no chapters scored yet", id="story_shape")
                story_shape.border_title = "STORY SHAPE"
                yield story_shape
                who_knows_what = WhoKnowsWhat("no secrets yet", id="who_knows_what")
                who_knows_what.border_title = "WHO KNOWS WHAT"
                yield who_knows_what
                causeway = Causeway("no causal edges yet", id="causeway")
                causeway.border_title = "CAUSEWAY"
                yield causeway
            with Vertical(id="right"):
                browser = StoryBrowser("Story", id="browser")
                browser.border_title = "STORY"
                yield browser
                with VerticalScroll(id="detail_scroll") as detail_scroll:
                    detail_scroll.border_title = "DETAIL"
                    yield Static("Select an item to view details.", id="detail")
        yield Static("AUTONOMY: loading…", id="statusbar")
        # compact=True drops Input's default tall border, which would consume
        # both edges of the single row #command gets and leave 0 content lines.
        yield Input(id="command", placeholder="command… (seed/focus/pause/resume)", compact=True)
        yield Footer()
```

Append to `novelizer/tui/app.tcss`:

```
#feed, #browser { border-title-style: bold; }
#proposals, #thread_board, #story_shape, #who_knows_what, #causeway, #detail_scroll { border-title-color: $text-muted; }
```

- [ ] **Step 4: Run test to verify it passes**

```
uv run pytest tests/tui/test_app_layout.py -v
```
Expected: all layout tests pass, including the three pre-existing ones.

- [ ] **Step 5: Commit**

```
git add novelizer/tui/app.py novelizer/tui/app.tcss tests/tui/test_app_layout.py
git commit -m "feat: border titles on every pane — THE ROOM / STORY / brain pane names"
```

---

### Task 7: Full-suite verification, zero warnings

**Files:**
- Modify: only whatever the suite run reveals (expected: nothing — Tasks 4–5 were designed to keep every existing string assertion passing; if any `tests/tui/` assertion on the old feed strings fails anyway, update the assertion to the new plain rendering **in this task**, per the spec's "updated in the same phase that changes their surface").

**Interfaces:**
- Consumes: everything above. Produces: a green suite.

- [ ] **Step 1: Run the full suite**

```
uv run pytest -q
```
Expected: all tests pass, `0 warnings` in the summary line. (Live-LLM tests skip themselves without the env; that's normal.)

- [ ] **Step 2: If anything fails, fix it** — string-assertion fallout only: update the failing assertion to match `render_event(...).plain` output (never weaken it to a bare truthiness check; assert on the meaningful substring). If a *warning* appears, fix its source (e.g. an unawaited coroutine in a new test), not the filter config.

- [ ] **Step 3: Re-run to verify green + zero warnings**

```
uv run pytest -q
```
Expected: `... passed` with no warnings summary block.

- [ ] **Step 4: Sanity-run the app render path once** (optional but cheap): `uv run pytest tests/tui -q` a final time to confirm the TUI subset alone is green.

- [ ] **Step 5: Commit** (only if Step 2 changed anything)

```
git add -A tests/
git commit -m "test: align remaining feed string assertions with render_event plain output"
```

---

## Self-review notes (spec coverage)

- Spec Phase 1 item ↔ task map: `identity.py` → Task 1; badges → Task 2; clamping + markdown → Task 3; `render_event` + line classes → Task 4; markup/Text handling, chapter rules, welcome block → Task 5; border titles → Task 6; existing-test surface updates → same-phase, Tasks 4/5/7.
- `app.messages` stays plain in every write site touched: welcome, rule, event, worker error (Task 5), command results and settings lines untouched.
- Type consistency verified: Task 4/5 consume exactly `identity_for`, `SPEAKER_WIDTH` (Task 1); `parse_source_badge` (Task 2); `clamp_text`, `md_inline` (Task 3); Task 5 consumes exactly `render_event`, `chapter_rule(number, title)`, `welcome_lines()`, `worker_error_line(name, exc)` (Task 4).
- `roster_summary` deliberately untouched (Global Constraints) — its tests assert raw agent-name strings that identity labels would break; Phase 3 owns that restyle.
