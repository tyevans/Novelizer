from __future__ import annotations

from novelizer.slug import slugify


def slugify_theme_name(name: str) -> str:
    """Turn a freeform theme title into a stable, id-safe slug.

    Lowercases, collapses runs of non-alphanumeric characters into single
    hyphens, and strips leading/trailing hyphens. Called exactly once, at
    theme.introduced time, to mint a theme's aggregate_id from the Author's
    or Editor's freeform title — see M5.2's theme identity rule (mirrors
    M3.1's thread rule and Locked decision 6's no-terminal-state design):
    no other theme.* event type ever mints or re-derives an id.
    """
    return slugify(name, "theme")
