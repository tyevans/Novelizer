from __future__ import annotations
from typing import Iterable, Optional
from novelizer.store.models import InspirationHandRecord

# Static defense in depth for minor characters the casting pool doesn't cover.
# These exact words appeared in 88% of AI-generated stories (Hamilton & Mimno,
# Cornell 2026); the corpora exclude them and the Author is told to as well.
AI_TELL_BAN_NOTE = (
    "Never name characters Elias, Elara, Mara, Thorne, or Voss, and avoid stock figures "
    "like lighthouse keepers, clockmakers, bakers, or quaint coastal villages — these are "
    "convergent AI cliches. Avoid near-variants of them too."
)

# How many recent consumed hands CharacterKeeper scans when matching a freshly
# minted character name to a dealt name (Task 6).
NAME_UPTAKE_HAND_WINDOW = 3


def casting_pool_note(hand: Optional[InspirationHandRecord]) -> str:
    if hand is None or not hand.names:
        return ""
    return (
        "\n\nCasting pool (binding): when you introduce a NEW named character, take their "
        "name from this list, exactly as written: " + "; ".join(hand.names)
    )


def inspiration_note(hand: Optional[InspirationHandRecord]) -> str:
    if hand is None:
        return ""
    parts = []
    if hand.professions:
        parts.append("professions: " + "; ".join(hand.professions))
    if hand.settings:
        parts.append("settings: " + "; ".join(hand.settings))
    if hand.beats:
        parts.append("story sparks: " + "; ".join(hand.beats))
    if not parts:
        return ""
    return (
        "\n\nInspiration hand (optional — weave in any that genuinely fit this chapter, "
        "ignore the rest): " + " | ".join(parts)
    )


def architect_settings_note(hand: Optional[InspirationHandRecord]) -> str:
    if hand is None or not hand.settings:
        return ""
    return "\n\nDrawn setting sparks (optional): " + "; ".join(hand.settings)


def name_uptake_matches(
    name: str, hands: Iterable[InspirationHandRecord]
) -> tuple[str, str] | None:
    """Match a minted character name against dealt names in `hands`.

    Returns (hand_id, dealt_item) for the most recent hand containing a match,
    or None. A match is the full dealt name, or its given-name token — prose
    routinely drops surnames, and a dropped surname is still uptake.
    """
    lowered = name.strip().lower()
    if not lowered:
        return None
    first_token = lowered.split(" ")[0]
    for hand in reversed(list(hands)):
        for dealt in hand.names:
            dealt_lower = dealt.lower()
            if dealt_lower == lowered or dealt_lower.split(" ")[0] == first_token:
                return (hand.id, dealt)
    return None
