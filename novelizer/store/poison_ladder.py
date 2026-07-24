from __future__ import annotations


class PoisonLadder:
    """Consecutive-failure budget, kept per event sequence.

    Shared by CanonIndexer and KGProjector because both stake the same
    property on it: a cursor pinned on an event that fails identically every
    time is a permanent stall, and under the strict background gate that stall
    pauses every agent in the room forever. The ladder knows nothing about
    what either projector writes to -- it only answers "have we retried this
    one enough to abandon it?".

    Counts are per sequence, never fleet-wide: three different events each
    failing once must not add up to a skip. They live in memory for the life
    of the projector instance (one per Runtime) and are deliberately not
    persisted -- forgetting across a restart costs at most one extra retry
    round, and the design accepts that.
    """

    def __init__(self, skip_after: int) -> None:
        # Floored at 1 because this is reachable from user config: a 0 would
        # read as "be patient forever" and instead abandon every event on its
        # first stumble, quietly gutting the index on one flaky endpoint.
        self._skip_after = max(1, skip_after)
        self._failures: dict[int, int] = {}

    @property
    def skip_after(self) -> int:
        return self._skip_after

    def record_failure(self, sequence: int) -> bool:
        """Tally one consecutive failure for `sequence`. True means the budget
        is spent on this pass -- abandon the event now rather than next time."""
        count = self._failures.get(sequence, 0) + 1
        if count >= self._skip_after:
            # The caller advances past this event, so its tally is dead weight.
            self._failures.pop(sequence, None)
            return True
        self._failures[sequence] = count
        return False

    def record_success(self, sequence: int) -> None:
        """A blip that recovers is not poison, so its tally clears."""
        self._failures.pop(sequence, None)
