"""Built-in structural beat templates.

Craft reference data — richer templates arrive as skills packs in M10; adopting a
framework mints Beat rows from one of these lists. Kishōtenketsu is deliberately
conflict-optional: templates must never require conflict or an antagonist; a
polarity mark on the turn denotes recontextualization, not battle.
"""

from pydantic import BaseModel, ConfigDict


class TemplateBeat(BaseModel):
    """A structural beat template for a story framework."""

    model_config = ConfigDict(frozen=True)

    slug: str
    """Beat identifier slug, e.g. 'midpoint'. Beat IDs mint as f'{blueprint_id}-{slug}'."""

    name: str
    """Human-readable beat name, e.g. 'Midpoint'."""

    ideal_pct: float
    """Ideal beat position as a percentage of total (0.0-1.0)."""

    tolerance_pct: float
    """Tolerance window as a percentage of total."""

    expected_polarity: str = ""
    """Expected polarity: '', 'up', 'down', or 'flip'. Empty string means no requirement."""


BEAT_TEMPLATES: dict[str, list[TemplateBeat]] = {
    "six-position": [
        TemplateBeat(slug="catalyst", name="Catalyst", ideal_pct=0.10, tolerance_pct=0.05),
        TemplateBeat(slug="threshold", name="Threshold", ideal_pct=0.25, tolerance_pct=0.05),
        TemplateBeat(
            slug="midpoint",
            name="Midpoint",
            ideal_pct=0.50,
            tolerance_pct=0.05,
            expected_polarity="flip",
        ),
        TemplateBeat(
            slug="low-point",
            name="Low Point",
            ideal_pct=0.75,
            tolerance_pct=0.05,
            expected_polarity="down",
        ),
        TemplateBeat(
            slug="final-turn",
            name="Final Turn",
            ideal_pct=0.80,
            tolerance_pct=0.05,
            expected_polarity="up",
        ),
        TemplateBeat(
            slug="climax", name="Climax", ideal_pct=0.90, tolerance_pct=0.05, expected_polarity="up"
        ),
    ],
    "kishotenketsu": [
        TemplateBeat(slug="ki", name="Ki", ideal_pct=0.05, tolerance_pct=0.08),
        TemplateBeat(slug="sho", name="Sho", ideal_pct=0.40, tolerance_pct=0.08),
        TemplateBeat(
            slug="ten",
            name="Ten",
            ideal_pct=0.75,
            tolerance_pct=0.08,
            expected_polarity="flip",
        ),
        TemplateBeat(slug="ketsu", name="Ketsu", ideal_pct=0.95, tolerance_pct=0.08),
    ],
}


def beat_window(ideal_pct: float, tolerance_pct: float, target_chapter_count: int) -> tuple[int, int]:
    """Calculate the chapter window for a beat within a target chapter count.

    Args:
        ideal_pct: Ideal position as a percentage (0.0-1.0).
        tolerance_pct: Tolerance as a percentage (0.0-1.0).
        target_chapter_count: Total number of chapters.

    Returns:
        Tuple of (min_chapter, max_chapter) both inclusive and 1-indexed.
    """
    center = round(ideal_pct * target_chapter_count)
    tol = max(1, round(tolerance_pct * target_chapter_count))
    return (max(1, center - tol), min(target_chapter_count, max(1, center + tol)))
