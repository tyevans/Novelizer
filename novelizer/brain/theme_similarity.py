from __future__ import annotations
from typing import Optional
from novelizer.store.embeddings import EmbeddingStore
from novelizer.store.models import ThemeRecord

THEME_SIMILARITY_SOURCE_TAG = "[source: theme_similarity]"

# chromadb's default distance metric here is squared L2 over normalized
# embeddings, which behaves like cosine distance in [0, 2]. 0.4 is chosen so
# titles that are near-paraphrases of each other (same theme, different
# wording -- e.g. "The Cost of Ambition" vs. "The Price of Ambition", which
# lands around 0.28 with the trigram-based fixture embedding) suggest a
# duplicate, while unrelated titles (observed around 0.65) do not.
DEFAULT_DISTANCE_THRESHOLD = 0.4


async def suggest_near_duplicate_theme(
    embedding_store: EmbeddingStore,
    new_theme: ThemeRecord,
    threshold: float = DEFAULT_DISTANCE_THRESHOLD,
) -> Optional[str]:
    """Pure suggestion, never an auto-merge (M5.2 Locked decision 8 / this
    milestone's non-goals): returns the id of an existing theme whose title
    embedding is within `threshold` distance of `new_theme.title`, or None
    if no theme collection entries exist or nothing is close enough. The
    caller is responsible for surfacing this as an Editor-facing suggestion
    (see novelizer/agents/base.py's `_commit_theme_intents`) -- this
    function never mutates any store.
    """
    results = await embedding_store.query_themes(new_theme.title, n=1)
    if not results:
        return None
    theme_id, distance = results[0]
    if theme_id == new_theme.id:
        return None
    if distance > threshold:
        return None
    return theme_id
