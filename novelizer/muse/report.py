from __future__ import annotations
from typing import Iterable, Optional
from novelizer.store.models import HandStatus, InspirationHandRecord, InspirationUptakeRecord


def _dealt_count(hand: InspirationHandRecord) -> int:
    return len(hand.names) + len(hand.professions) + len(hand.settings) + len(hand.beats)


def uptake_summary(
    hands: Iterable[InspirationHandRecord], uptake: Iterable[InspirationUptakeRecord]
) -> str:
    """The feature's health metric: beat draws are optional inspiration, so if
    this trends toward zero the director raises authority via settings rather
    than the feature silently doing nothing (spec: accepted risk, tracked)."""
    consumed = [h for h in hands if h.status == HandStatus.consumed]
    dealt = sum(_dealt_count(h) for h in consumed)
    if dealt == 0:
        return "No consumed hands yet — uptake unknown."
    used = len({(u.hand_id, u.kind, u.item) for u in uptake})
    return f"Uptake: {used}/{dealt} dealt items landed in prose across {len(consumed)} consumed hands ({100 * used // dealt}%)."


def muse_status_report(
    active: Optional[InspirationHandRecord],
    hands: Iterable[InspirationHandRecord],
    uptake: Iterable[InspirationUptakeRecord],
) -> str:
    if active is None:
        head = "No active hand (the Muse deals within its next cycle)."
    else:
        head = (
            f"Active hand [{active.id[:8]}] era={active.era}:\n"
            f"  names: {'; '.join(active.names)}\n"
            f"  professions: {'; '.join(active.professions)}\n"
            f"  settings: {'; '.join(active.settings)}\n"
            f"  beats: {'; '.join(active.beats)}"
        )
    return f"{head}\n{uptake_summary(hands, uptake)}"
