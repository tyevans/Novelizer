from __future__ import annotations
import logging
import secrets
import uuid
from novelizer.agents.base import BaseAgent
from novelizer.canon.committer import Committer
from novelizer.canon.events import EventType, InspirationDrawn
from novelizer.canon.read_store import ReadStore
from novelizer.muse.corpus import load_corpora
from novelizer.muse.draws import DEFAULT_ERA, deal_hand

logger = logging.getLogger(__name__)


def _exclusion_window(hands, n: int) -> set[str]:
    """Compute the set of items to exclude from the last `n` dealt hands.

    n <= 0 means no exclusion window at all (not "exclude everything" —
    a naive `hands[-0:]` slice would wrongly return the whole list).
    """
    recent = hands[-n:] if n > 0 else []
    return {
        item
        for hand in recent
        for item in (*hand.names, *hand.professions, *hand.settings, *hand.beats)
    }


class Muse(BaseAgent):
    """Deals seeded hands of corpus draws as inspiration.* events.

    The one agent with no LLM: poll the read projection, top up the hand,
    commit. Corpora load at construction so a bad data file fails Runtime
    startup, never mid-novel. Keeps exactly one unconsumed hand ahead of the
    Author; the Author consumes it at chapter commit, which makes readiness
    flip back to 0.9 and the next cycle deal a fresh hand.
    """

    def __init__(
        self,
        read_store: ReadStore,
        committer: Committer,
        interval: int = 60,
        era: str = DEFAULT_ERA,
        exclusion_hands: int = 3,
        personality: str = "",
    ) -> None:
        super().__init__(None, read_store, committer, interval, name="muse", personality=personality)
        self._corpora = load_corpora()
        self._era = era
        self._exclusion_hands = exclusion_hands

    async def readiness(self) -> float:
        return 0.0 if await self._read.get_active_hand() is not None else 0.9

    async def run_once(self) -> None:
        if await self._read.get_active_hand() is not None:
            return
        await self.deal_fresh_hand()

    async def deal_fresh_hand(self) -> InspirationDrawn:
        """Deal and commit a new hand unconditionally. Public: the director's
        `:muse reroll` calls this right after superseding the active hand,
        without waiting for the projector to catch up."""
        hands = await self._read.list_hands()
        exclude = _exclusion_window(hands, self._exclusion_hands)
        seed = secrets.randbits(63)
        hand = deal_hand(self._corpora, seed, self._era, exclude, str(uuid.uuid4()))
        await self._committer.commit(self.name, EventType.INSPIRATION_DRAWN, hand.hand_id, hand)
        await self._remark(f"a fresh hand on the table: {', '.join(hand.names)}")
        logger.info("muse dealt hand %s (seed=%d, era=%s)", hand.hand_id, seed, hand.era)
        return hand


from novelizer.agents.registry_types import AgentContext, AgentSpec, AgentTier


def _construct(ctx: AgentContext) -> Muse:
    return Muse(
        ctx.read, ctx.committer,
        interval=ctx.settings.muse_interval,
        era=ctx.settings.muse_era,
        exclusion_hands=ctx.settings.muse_exclusion_hands,
        personality=ctx.personalities.get("muse", ""),
    )


SPEC = AgentSpec(name="muse", tool_grant=None, construct=_construct,
                 tier=AgentTier.FULL)
