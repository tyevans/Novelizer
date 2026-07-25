from __future__ import annotations

from novelizer.slug import slugify

TERMINAL_STATES: set[str] = {"paid_off", "abandoned"}


def active_thread_ids(threads) -> set[str]:
    """The ids `commit_thread_intents` may be given: known and non-terminal.

    That helper's contract is that a citing intent naming a terminal id is
    dropped, so the caller owes it a filtered set -- passing every thread makes
    the guard unreachable and lets a touch/pay_off land on a finished thread as a
    permanent phantom event in the log. Every caller expressed this filter
    inline, and the Continuity Checker's mining path was the one that forgot;
    naming the rule once removes the chance to forget it again.
    """
    return {t.id for t in threads if t.state.value not in TERMINAL_STATES}


def slugify_thread_name(name: str) -> str:
    """Turn a freeform thread name into a stable, id-safe slug.

    Lowercases, collapses runs of non-alphanumeric characters into single
    hyphens, and strips leading/trailing hyphens. Called exactly once, at
    thread.planted time, to mint a thread's aggregate_id from the Author's
    or Editor's freeform name — see M3.1's thread identity rule: no other
    thread.* event type ever mints or re-derives an id.
    """
    return slugify(name, "thread")
