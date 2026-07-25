"""The rule that every production caller of the secret-citations committer
supplies a character roster.

commit_secret_citations validates the secret id an agent cites, and -- when
given a `character_ids` roster -- that the cited character exists. The roster
is optional on purpose: plenty of tests commit citations with no cast to check
against, and forcing a roster there would mean threading a meaningless set
through thirty call sites. `character_ids=None` means "this caller genuinely
has no roster", never "skip the check because it is inconvenient".

Scoped to CITATIONS since the F6 split: commit_secret_plants takes no
character at all, so a roster would be meaningless there. Watching the plant
committer too would make this guard weaker, not stronger -- it would demand a
kwarg the function does not accept, and the only way to satisfy it would be to
add one nothing reads.

That leniency is exactly what needs guarding in production. A hallucinated
character_id writes a secret_knowledge row for a character who does not
exist: the knowledge matrix's known_by gains a phantom while the character
the agent meant still reads "unknown", so the LeakDetector flags that
character's next legitimate use as a leak. Five production call sites pass a
roster today (Author, Editor, CharacterKeeper, ContinuityChecker, chat) --
nothing made a sixth do so. This is the guard that does.
"""
from __future__ import annotations

import ast
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
PRODUCTION_ROOT = REPO_ROOT / "novelizer"

# Both spellings: the module-level helper, and BaseAgent's forwarding wrapper.
COMMITTER_NAMES = {"commit_secret_citations", "_commit_secret_citations"}
ROSTER_KWARG = "character_ids"


def _callee_name(node: ast.Call) -> str:
    """The bare function name of a call, however it is reached --
    `commit_secret_citations(...)`, `intent_helpers.commit_secret_citations(...)`
    and `self._commit_secret_citations(...)` all reduce to the same name."""
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def find_calls_missing_roster(source: str) -> list[int]:
    """Line numbers of calls to the citations committer that omit the roster.

    A call that forwards the kwarg through (`character_ids=character_ids`)
    counts as supplying it -- BaseAgent's wrapper is a conduit, not a caller
    that could invent a roster of its own. `**kwargs` forwarding also counts,
    since the roster may be inside it and ast cannot tell.
    """
    missing = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call) or _callee_name(node) not in COMMITTER_NAMES:
            continue
        supplied = {kw.arg for kw in node.keywords}
        if ROSTER_KWARG in supplied or None in supplied:
            continue
        missing.append(node.lineno)
    return missing


def test_every_production_call_site_supplies_a_character_roster():
    offenders = []
    for path in sorted(PRODUCTION_ROOT.glob("**/*.py")):
        for lineno in find_calls_missing_roster(path.read_text()):
            offenders.append(f"{path.relative_to(REPO_ROOT)}:{lineno}")
    assert offenders == [], (
        "these calls commit secret citations without a character roster, so a "
        f"hallucinated character_id would be written unchecked: {offenders}"
    )


def test_at_least_one_production_call_site_is_actually_detected():
    """A detector that matches nothing passes forever. This pins that the
    walk really does reach the production call sites, so the guard above is
    green because the call sites are correct -- not because nothing was
    examined."""
    seen = set()
    for path in PRODUCTION_ROOT.glob("**/*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Call) and _callee_name(node) in COMMITTER_NAMES:
                seen.add(path.relative_to(REPO_ROOT).as_posix())
    assert seen == {
        "novelizer/agents/author.py",
        "novelizer/agents/base.py",
        "novelizer/agents/character_keeper.py",
        "novelizer/agents/continuity_checker.py",
        "novelizer/agents/editor.py",
        "novelizer/chat/service.py",
    }, f"the set of modules committing secret citations changed: {sorted(seen)}"


def test_the_detector_catches_a_call_site_that_forgets_the_roster():
    """The negative case, so the guard is known to fail when it should."""
    source = (
        "async def work(self):\n"
        "    await self._commit_secret_citations(citations, active_secret_ids)\n"
    )
    assert find_calls_missing_roster(source) == [2]


def test_the_detector_accepts_a_forwarded_roster():
    forwarded = (
        "async def _commit_secret_citations(self, citations, ids, character_ids=None):\n"
        "    await intent_helpers.commit_secret_citations(\n"
        "        self._committer, self.name, citations, ids, character_ids=character_ids,\n"
        "    )\n"
    )
    assert find_calls_missing_roster(forwarded) == []
    splatted = "commit_secret_citations(c, name, citations, ids, **overrides)\n"
    assert find_calls_missing_roster(splatted) == []
