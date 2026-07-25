"""Is this tool output worth handing to a Markdown renderer?

Two predicates, deliberately separate: a path is authoritative when we
have one, and the content sniff is the fallback for tools that return
markdown without naming a file. Both are pure -- no Rich, no Textual --
so the decision is unit-testable apart from any rendering.
"""
from __future__ import annotations
import re

_MD_EXTENSIONS = (".md", ".markdown")

_SIGNALS = (
    re.compile(r"^#{1,6} \S", re.MULTILINE),      # ATX heading
    re.compile(r"^```", re.MULTILINE),            # fenced code
    re.compile(r"^\s*[-*+] \S", re.MULTILINE),    # bullet list
    re.compile(r"^\s*\d+\. \S", re.MULTILINE),    # ordered list
    re.compile(r"^\s*\|.*\|\s*$", re.MULTILINE),  # table row
    re.compile(r"^\s*> \S", re.MULTILINE),        # blockquote
)

# Two signals, not one: a lone "# comment" line in a log or a lone "- " in
# prose is not a markdown document, and rendering it as one reflows text
# the reader wanted verbatim.
_MIN_SIGNALS = 2


def ext_is_markdown(path: str) -> bool:
    return str(path).strip().lower().endswith(_MD_EXTENSIONS)


def _looks_like_json(text: str) -> bool:
    s = text.strip()
    return len(s) >= 2 and s[0] in "{[" and s[-1] in "}]"


def looks_markdown(text: str) -> bool:
    if not text or not text.strip():
        return False
    # JSON is full of braces, colons and quoted "#" strings that trip the
    # heading and table patterns; it is never markdown.
    if _looks_like_json(text):
        return False
    normalized = text.replace("\r\n", "\n")
    return sum(1 for p in _SIGNALS if p.search(normalized)) >= _MIN_SIGNALS
