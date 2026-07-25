"""The Flag aggregate: the lifecycle rules for the review queue.

A flag moves along two independent axes — a *status* (open, then one of the
three closed statuses) and an *escalation* (raised, then cleared). Four
unrelated modules used to drive those transitions by hand: the Curator and
Retconner (owning agents that decline or resolve), Triage (dismisses, ages, or
escalates on severity) and the escalations screen (a human clearing by hand).
Each had spelled the rules out inline, and they had drifted — two copies of the
escalation threshold, two shapes for the same clear, and missing guards in
whichever copy was written last.

This module names those rules once. `may_*` are the guards, `should_*` the
policy predicates, and `mark_*` the transitions: each returns a NEW flag with
the transition applied and raises ValueError if its own guard does not hold, so
a caller that skips the guard fails loudly instead of appending a phantom event
to the log. Guarding here rather than in
novelizer.canon.projections.flags is deliberate — the projection is a faithful
fold of the log, so an event that should not exist has to be stopped upstream of
the commit, not suppressed on the way out.
"""
from __future__ import annotations

from novelizer.store.models import Flag, FlagStatus

# How many owning-agent decline cycles a flag absorbs before it escalates
# regardless of its assessed severity. One definition: the Curator and the
# Retconner both used to carry their own copy of this literal.
FAILURE_ESCALATION_THRESHOLD = 3

# The closed statuses. A flag in one of these has been decided and no further
# status transition may land on it.
TERMINAL_FLAG_STATES: set[FlagStatus] = {
    FlagStatus.resolved, FlagStatus.rejected, FlagStatus.stale,
}

# How many of its own rejections an agent carries in its poll context. Rejected
# flags are terminal, so the list only ever grows -- unbounded, it would crowd
# out the prompt it is meant to inform. Five is the smallest window that still
# spans a measured run's rejections (5 in the window that produced 17 filings),
# so an agent sees the whole of its recent record rather than a sample of it.
RECENT_REJECTION_LIMIT = 5


def is_terminal(flag: Flag) -> bool:
    """Whether the flag's status has already been decided."""
    return flag.status in TERMINAL_FLAG_STATES


def may_decide(flag: Flag) -> bool:
    """Whether a status transition (resolve / decline / dismiss / stale) may land.

    Only an open flag may be decided; deciding a closed one twice would append a
    second closing event that says nothing new about canon.
    """
    return not is_terminal(flag)


def may_escalate(flag: Flag) -> bool:
    """Whether an escalation may be raised: nothing is currently up.

    Escalation is orthogonal to status, like the clear below — the
    repeated-failure trigger escalates a flag it has just REJECTED, which is the
    whole point of that path: the human needs to see an issue its owning agent
    keeps failing to action. So only the double-raise is refused here. Both
    triggers — Triage's "critical" verdict and the repeated-failure threshold —
    share this guard; only the trigger differs.
    """
    return not flag.escalated


def may_clear_escalation(flag: Flag) -> bool:
    """Whether an active escalation exists to clear.

    Status is deliberately not consulted: the owning agents resolve a flag and
    then clear its escalation, so the flag is already terminal by the time the
    clear lands. What matters is that there is something to clear.
    """
    return flag.escalated


def should_escalate_after_failure(flag: Flag) -> bool:
    """Whether this flag's accumulated failed attempts warrant escalation.

    Read AFTER the failing attempt has been counted (i.e. on the flag returned
    by `mark_declined`), which is why the comparison is `>=` rather than `>`.
    """
    return flag.failed_attempts >= FAILURE_ESCALATION_THRESHOLD and may_escalate(flag)


def own_rejections(
    flags: list[Flag], *, filed_by: str, limit: int = RECENT_REJECTION_LIMIT
) -> list[Flag]:
    """The filing agent's own most recent rejections, oldest first.

    The read side of `filed_by`, which was written at eight filing sites and
    read at none: an agent could not learn that a judgement it made had been
    thrown out, so the fleet could re-file the same rejected finding forever.
    Stated here rather than per agent for the reason
    `novelizer.canon.threads.active_thread_ids` was extracted -- seven callers
    means seven chances to filter it differently.

    "Recent" is the tail of the caller's list, which `ReadStore.list_flags`
    orders by rowid, i.e. by the order the flags were FILED (rowid is stable
    across the upsert). So this is the agent's last `limit` filings that ended
    in a rejection, not the last `limit` rejections to be decided; the two
    differ only when an old flag is decided after a newer one, and filing order
    is the order the agent itself remembers thinking in.

    Rejected only. `stale` is also a closed status but says nothing about the
    judgement -- it means no owning agent ever looked -- and `resolved` means
    the finding was right. A blank `filed_by` never matches: flags projected
    before the field existed carry one, and they belong to no agent.
    """
    if not filed_by:
        return []
    mine = [f for f in flags if f.filed_by == filed_by and f.status == FlagStatus.rejected]
    return mine[-limit:]


def resolution_note(flag: Flag) -> str:
    """The resolver's own words on a closed flag, or "" when there are none.

    `proposed_resolution` holds two different things over a flag's life:
    `mark_declined` overwrites it with the decliner's "[resolution] reason",
    while `mark_dismissed` leaves the FILER's own proposal untouched. Only the
    decline path counts a failed attempt, so `failed_attempts` is what tells
    the two apart -- without this the feedback loop would quote an agent's own
    proposal back at it as the reason its flag was thrown out.
    """
    return flag.proposed_resolution if flag.failed_attempts else ""


def mark_declined(flag: Flag, *, by: str, resolution: str, reason: str) -> Flag:
    """Close the flag because the owning agent could not action it.

    Counts a failed attempt — this is the path that feeds
    `should_escalate_after_failure` — and records the machine-readable
    `resolution` ahead of the freeform reason so the filing agent's log says why.
    """
    _require(may_decide(flag), f"flag {flag.id} is already {flag.status}")
    return flag.model_copy(update={
        "status": FlagStatus.rejected,
        "resolved_by": by,
        "proposed_resolution": f"[{resolution}] {reason}" if reason else f"[{resolution}]",
        "failed_attempts": flag.failed_attempts + 1,
    })


def mark_dismissed(flag: Flag, *, by: str) -> Flag:
    """Close the flag because it was judged not to be a real issue.

    Distinct from `mark_declined`: nobody failed at anything, so no attempt is
    counted and the flag cannot escalate off the back of it.
    """
    _require(may_decide(flag), f"flag {flag.id} is already {flag.status}")
    return flag.model_copy(update={"status": FlagStatus.rejected, "resolved_by": by})


def mark_resolved(flag: Flag, *, by: str) -> Flag:
    """Close the flag because the concern it raised has been repaired."""
    _require(may_decide(flag), f"flag {flag.id} is already {flag.status}")
    return flag.model_copy(update={"status": FlagStatus.resolved, "resolved_by": by})


def mark_stale(flag: Flag, *, by: str, triage_passes: int) -> Flag:
    """Close an unowned flag that has aged out of the catch-all triage queue."""
    _require(may_decide(flag), f"flag {flag.id} is already {flag.status}")
    return flag.model_copy(update={
        "status": FlagStatus.stale,
        "resolved_by": by,
        "triage_passes": triage_passes,
    })


def mark_escalated(flag: Flag) -> Flag:
    """Raise the flag for human attention."""
    _require(may_escalate(flag), f"flag {flag.id} cannot be escalated")
    return flag.model_copy(update={"escalated": True})


def mark_escalation_cleared(flag: Flag, *, by: str, note: str | None = None) -> Flag:
    """Take the flag back off the escalations queue.

    One shape for both clearers: `by` keeps the genuine distinction between an
    owning agent auto-clearing on resolution ("agent") and a human clearing by
    hand ("human"), and the optional note is available to either rather than
    being a quirk of the screen that happened to offer an input box.
    """
    _require(may_clear_escalation(flag), f"flag {flag.id} is not escalated")
    return flag.model_copy(update={
        "escalated": False,
        "escalation_cleared_by": by,
        "escalation_clear_note": note,
    })


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)
