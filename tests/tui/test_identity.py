from novelizer.tui.identity import IDENTITIES, SPEAKER_WIDTH, identity_for


def test_every_fleet_agent_plus_director_and_system_present():
    assert set(IDENTITIES) == {
        "author", "editor", "world_architect", "character_keeper",
        "continuity_checker", "retconner", "structure_analyst", "plotter", "muse",
        "summarizer", "curator", "triage", "flaglabeler", "director", "system",
    }


def test_glyphs_match_spec_table_verbatim():
    expected = {
        "author": "✎", "editor": "§", "world_architect": "⌂",
        "character_keeper": "♥", "continuity_checker": "⚖",
        "retconner": "↺", "structure_analyst": "∿", "plotter": "⌖", "muse": "✦",
        "summarizer": "≡", "curator": "❖", "triage": "⑂", "flaglabeler": "⚑",
        "director": "★", "system": "·",
    }
    assert {k: v.glyph for k, v in IDENTITIES.items()} == expected


def test_labels_keep_existing_feed_names():
    expected = {
        "author": "Author", "editor": "Editor", "world_architect": "Architect",
        "character_keeper": "Keeper", "continuity_checker": "Continuity",
        "retconner": "Retconner", "structure_analyst": "Analyst", "plotter": "Plotter",
        "muse": "Muse", "summarizer": "Summary", "curator": "Curator",
        "triage": "Triage", "flaglabeler": "Flags",
        "director": "Director", "system": "System",
    }
    assert {k: v.label for k, v in IDENTITIES.items()} == expected


def test_agent_colors_are_unique_and_director_system_are_weight_only():
    agent_styles = [v.style for k, v in IDENTITIES.items() if k not in ("director", "system")]
    assert len(agent_styles) == len(set(agent_styles))
    assert IDENTITIES["director"].style == "bold"
    assert IDENTITIES["system"].style == "dim"


def test_styles_are_valid_rich_styles():
    from rich.style import Style
    for ident in IDENTITIES.values():
        Style.parse(ident.style)  # raises on an invalid style string


def test_styles_are_valid_textual_styles():
    """Identity styles reach Textual's parser too (engine-room tab titles are
    Content.styled), and Textual rejects Rich's 256-color names -- 'gold3' et
    al parse fine in Rich but silently render uncolored in Textual."""
    from textual.style import Style
    for ident in IDENTITIES.values():
        Style.parse(ident.style)  # raises on an invalid style string


def test_identity_for_known_agent_returns_registry_entry():
    assert identity_for("author") is IDENTITIES["author"]


def test_identity_for_unknown_agent_falls_back_to_dim_title_case():
    unknown = identity_for("mystery_agent")
    assert unknown.label == "Mystery Agent"
    assert unknown.glyph == "·"
    assert unknown.style == "dim"


def test_speaker_width_fits_every_glyph_label_pair():
    for ident in IDENTITIES.values():
        assert len(f"{ident.glyph} {ident.label}") <= SPEAKER_WIDTH


def test_every_glyph_is_single_cell_with_single_ascii_fallback():
    for ident in IDENTITIES.values():
        assert len(ident.glyph) == 1
        assert len(ident.fallback) == 1 and ident.fallback.isascii()


def test_identity_for_empty_string_falls_back_to_system():
    ident = identity_for("")
    assert ident.label == "System"
    assert ident.glyph == "·"
    assert ident.style == "dim"


from tui_kit.contracts import AgentTheme
from novelizer.tui.identity import AGENT_NAMES, NOVELIZER_AGENT_THEME


def test_agent_names_matches_the_scheduler_registry_order():
    """Compared against the registry, not a copy of it.

    This test used to restate the tuple literally, so when the fleet grew it
    agreed with the stale value instead of catching it -- AGENT_NAMES sat at 9
    of 13 agents and the four missing ones were never drawn in the Engine Room.
    """
    from novelizer.agents.registry import AGENT_REGISTRY

    assert AGENT_NAMES == tuple(spec.name for spec in AGENT_REGISTRY)


def test_novelizer_agent_theme_satisfies_the_agent_theme_protocol():
    theme: AgentTheme = NOVELIZER_AGENT_THEME
    assert theme.glyph("author") == "✎"
    assert theme.label("author") == "Author"
    assert theme.style("author") == "#d7af00"


def test_novelizer_agent_theme_verb_uses_the_verb_table_with_a_fallback():
    assert NOVELIZER_AGENT_THEME.verb("author") == "drafting"
    assert NOVELIZER_AGENT_THEME.verb("muse") == "drawing inspiration"
    # The fallback is asserted against a name that will never be in the table;
    # using a real agent made this pass only for as long as that agent had no
    # verb, so filling the table in turned a mechanism test red.
    assert NOVELIZER_AGENT_THEME.verb("not_an_agent") == "working"


def test_every_fleet_agent_has_its_own_verb():
    """A fleet agent showing the generic "working" reads as a stalled lane."""
    from novelizer.agents.registry import AGENT_REGISTRY

    generic = [
        spec.name for spec in AGENT_REGISTRY
        if NOVELIZER_AGENT_THEME.verb(spec.name) == "working"
    ]
    assert not generic, generic
