"""Prompt surfaces shared by every LLM agent.

Home for the text every agent inherits, so no agent is a prompt hub for the
others (the retrieval note used to live in author.py and be imported by six
siblings). Machinery constants that are behaviour rather than text --
GRAPH_RECURSION_LIMIT, PASS_BACKOFF_MULTIPLIER -- stay in base.py.

Design rationale is in docs/agent-prompting/proposal-fleet-shared.md §2.1-§2.2.
Two failure modes drive this text: agents working from the pushed summary
instead of reading canon (under-retrieval), and agents browsing past the
evidence they already have (turn-burning).
"""
from __future__ import annotations

_RETRIEVAL_NOTE_PREFIX = (
    "\n\n## Canon access\n"
    "You have file tools over the story canon (ls, read_file, grep, glob) and semantic "
    "search (search_canon). Work index-then-read: grep/glob for an exact name, slug, or "
    "phrase, search_canon for a theme or 'where did X happen' when you don't know the "
    "words — locate the file, then read_file the span you need. "
)

# Only for agents whose pushed context carries a chapter index; without one the
# sentence would point at nothing.
_RETRIEVAL_NOTE_MAP_SENTENCE = (
    "The chapter list below is an INDEX, not the source of truth: do NOT write or flag "
    "from a pushed summary alone — read the chapter or canon file in full before you "
    "commit any claim about it. "
)

_RETRIEVAL_NOTE_SUFFIX = (
    "Ground every id you emit in a file you actually read, and cite ids exactly as shown "
    "in frontmatter or search results. Once you can point to the line that supports your "
    "finding, stop searching and emit — don't browse past the evidence."
)

RETRIEVAL_NOTE_BASE = _RETRIEVAL_NOTE_PREFIX + _RETRIEVAL_NOTE_SUFFIX

RETRIEVAL_NOTE = _RETRIEVAL_NOTE_PREFIX + _RETRIEVAL_NOTE_MAP_SENTENCE + _RETRIEVAL_NOTE_SUFFIX

DEFAULT_PASS_REMARK = "Nothing needs my attention — carry on with the story."

# Three-way rather than act/skip: the middle branch ("confirm first") is where
# abstain calibration is won, and both outer branches are named as failures so
# the agent optimises for a correct call, not for looking busy.
PASS_PROMPT_INSTRUCTION = (
    "\n\n## When to act vs stand aside\n"
    "First name the concrete changes in your lane since your last pass — a new or revised "
    "chapter, a new intent, a changed sheet. Then decide:\n"
    "- A real change you can act on with evidence: act on it. Staying silent on a genuine "
    "development is a failure, not caution.\n"
    "- Nothing changed in your lane, or nothing you can ground in the canon: set "
    "no_action=true, leave every list empty, and give a one-line feed_note in character "
    "saying you're standing aside. A correct stand-aside is a SUCCESS; inventing a "
    "marginal item to look busy is a failure.\n"
    "- Something you suspect but cannot yet confirm: read the canon to confirm before you "
    "emit. Never emit on suspicion alone."
)
