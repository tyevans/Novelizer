"""Pure proposal rendering: open-proposal records -> the banner line and
(Task 4) the approval modal's rows and payload context. No Textual imports,
no I/O — same seam as the other *_model.py modules."""
from __future__ import annotations

from rich.text import Text

from novelizer.canon.autonomy import Proposal
from novelizer.tui.identity import SPEAKER_WIDTH, identity_for

# The one high-contrast line on the dashboard (spec Zone 3: "then it is the
# most visible thing on screen").
BANNER_STYLE = "bold black on gold3"


def banner_line(count: int) -> Text:
    """'▼ 2 proposals awaiting approval — press a'. Only called with
    count >= 1 — the app hides the banner widget entirely when the queue is
    empty (zero rows spent)."""
    noun = "proposal" if count == 1 else "proposals"
    return Text(f"▼ {count} {noun} awaiting approval — press a", style=BANNER_STYLE)


DIM = "dim"

# Human payload fields, in feed_model's fallback order; ids never summarize.
_SUMMARY_KEYS = ("title", "name", "note", "description", "body")
SUMMARY_WIDTH = 60
_VALUE_WIDTH = 160
_SKIP_CONTEXT_KEYS = {"created_at", "provenance"}


def _one_line(value: object, width: int) -> str:
    collapsed = " ".join(str(value).split())
    return collapsed if len(collapsed) <= width else collapsed[: width - 1] + "…"


def payload_summary(payload: dict) -> str:
    """One collapsed, clipped line naming the proposed payload — first
    non-empty human field, or '' when the payload has none."""
    for key in _SUMMARY_KEYS:
        if payload.get(key):
            return _one_line(payload[key], SUMMARY_WIDTH)
    return ""


def proposal_row(p: Proposal) -> Text:
    """One id-free queue row: proposing agent (glyph + label in the agent's
    color, feed-aligned) → target event type, dim payload summary."""
    ident = identity_for(p.proposing_agent)
    row = Text()  # spans, not a base style — the tests assert on row.spans
    row.append(f"{ident.glyph} {ident.label}".ljust(SPEAKER_WIDTH), style=ident.style)
    row.append(f"→ {p.target_event_type}")
    summary = payload_summary(p.payload)
    if summary:
        row.append(f"  {summary}", style=DIM)
    return row


def _is_bookkeeping(key: str) -> bool:
    return (
        key == "id"
        or key.endswith("_id")
        or key.endswith("_ids")
        or key in _SKIP_CONTEXT_KEYS
    )


def proposal_context(p: Proposal) -> Text:
    """Full payload context for the highlighted row: bold header, then one
    'key: value' line per human payload field. Ids/slugs and bookkeeping
    fields are skipped — names, not ids — and empty values are omitted."""
    ident = identity_for(p.proposing_agent)
    ctx = Text()  # spans, not a base style — bold must not bleed into values
    ctx.append(f"{ident.label} proposes {p.target_event_type}", style="bold")
    for key, val in p.payload.items():
        if _is_bookkeeping(key) or val in (None, "", [], {}):
            continue
        ctx.append("\n")
        ctx.append(f"{key}: ", style=DIM)
        ctx.append(_one_line(val, _VALUE_WIDTH))
    return ctx
