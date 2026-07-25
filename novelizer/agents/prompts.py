"""Prompt surfaces shared by every LLM agent.

Home for the text every agent inherits, so no agent is a prompt hub for the
others (the retrieval note used to live in author.py and be imported by six
siblings). Machinery constants that are behaviour rather than text --
GRAPH_RECURSION_LIMIT -- stay in base.py.

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
    "words — locate the file, then read_file the span you need. read_file returns only the "
    "FIRST 100 LINES unless you pass `limit`: pass `limit=2000` whenever you mean to read a "
    "file whole, and treat any result ending in a TRUNCATED notice as a partial view — make "
    "the follow-up call the notice spells out before you judge what the file says. The tree "
    "has exactly six "
    "top-level directories — /chapters, /characters, /world, /threads, /secrets, /themes — "
    "and no /canon prefix: paths start at those roots. Filenames are the record's full "
    "title slugged: lowercase, each run of spaces or punctuation becomes one dash, leading "
    "articles kept — 'The Mourning Courts of Vael' is world/the-mourning-courts-of-vael.md. "
    "Chapter files add an ordinal prefix and are never named by chapter number: "
    "`chapters/001-the-salt-road.md`, not `chapters/ch01.md` or `chapters/ch1.md`; use "
    "`chapters/*.md` to list them all. One file is DERIVED rather than slugged from a "
    "record's title, so no title leads to it: `/secrets/_dramatic-irony.md` is the "
    "dramatic-irony ledger — what the reader already knows versus what each character on "
    "the page still doesn't, per secret, in chapter order. Read it when you want a scene "
    "to play its irony on purpose; it says so plainly when the story has no gaps yet. "
    "Never guess a path from memory — if a read misses, "
    "ls or glob the directory and use a path listed there. "
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
    "in frontmatter or search results. When an intent you emit has an `evidence` field, "
    "put the chNNN handle or canon file path you read there — an intent that cites "
    "existing canon with no evidence reads as a guess. Once you can point to the line "
    "that supports your finding, stop searching and emit — don't browse past the evidence."
)

RETRIEVAL_NOTE_BASE = _RETRIEVAL_NOTE_PREFIX + _RETRIEVAL_NOTE_SUFFIX

RETRIEVAL_NOTE = _RETRIEVAL_NOTE_PREFIX + _RETRIEVAL_NOTE_MAP_SENTENCE + _RETRIEVAL_NOTE_SUFFIX

# Appended in every tooled builder's backend branch (same seam as the
# retrieval notes). The last sentence is a deliberate inline summary: the
# note must do some good even when the agent never opens the file.
OUTPUT_CONVENTIONS_NOTE = (
    "\n\n## Output contract\n"
    "Your structured output has a field-by-field contract: read "
    "/skills/output-conventions/SKILL.md before your first emit if you are unsure "
    "what belongs in which field. The short version: titles and names are one "
    "short line; bodies go in prose/body fields and nowhere else; never invent "
    "markup tags inside a field; cite only ids you actually saw."
)

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
