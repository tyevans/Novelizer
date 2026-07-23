# tests/substrate/test_public_api.py
import substrate

EXPECTED_PUBLIC_API = [
    "AgentContext",
    "AgentSpec",
    "EventTypeRegistry",
    "EventTypeSpec",
    "GatingTier",
    "PostgresDepsStore",
    "PostgresEmbeddingStore",
    "PostgresEventStore",
    "ProjectionCatalog",
    "ProjectionSpec",
    "SubagentGrant",
    "ToolGrant",
    "is_gated",
]


def test_all_matches_expected_public_api():
    assert substrate.__all__ == EXPECTED_PUBLIC_API


def test_every_name_in_all_is_importable_from_top_level():
    for name in substrate.__all__:
        assert hasattr(substrate, name), f"{name} listed in __all__ but not importable from substrate"
