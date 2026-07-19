from novelizer.chat.personas import CHAT_PERSONAS, resolve_agent_name
from novelizer.chat.schemas import ChatReply

CANONICAL = {
    "author", "editor", "world_architect", "character_keeper",
    "continuity_checker", "retconner", "structure_analyst",
}


def test_personas_cover_exactly_the_seven_agents():
    assert set(CHAT_PERSONAS) == CANONICAL
    for persona in CHAT_PERSONAS.values():
        assert persona.role_prompt.strip()


def test_intent_permissions_mirror_autonomous_behavior():
    assert CHAT_PERSONAS["author"].knowledge_actions == frozenset({"plant", "learn", "reveal", "uses"})
    assert CHAT_PERSONAS["editor"].knowledge_actions == frozenset({"plant", "learn", "reveal", "uses"})
    assert CHAT_PERSONAS["character_keeper"].knowledge_actions == frozenset({"learn"})
    for name in ("world_architect", "continuity_checker", "retconner", "structure_analyst"):
        p = CHAT_PERSONAS[name]
        assert not p.allow_threads and not p.allow_themes and not p.allow_causal
        assert p.knowledge_actions == frozenset()
    assert CHAT_PERSONAS["author"].allow_threads and CHAT_PERSONAS["author"].allow_causal
    assert not CHAT_PERSONAS["character_keeper"].allow_threads


def test_resolve_agent_name_canonical_aliases_and_case():
    assert resolve_agent_name("author") == "author"
    assert resolve_agent_name("Keeper") == "character_keeper"
    assert resolve_agent_name("architect") == "world_architect"
    assert resolve_agent_name("continuity") == "continuity_checker"
    assert resolve_agent_name("analyst") == "structure_analyst"
    assert resolve_agent_name("retcon") == "retconner"
    assert resolve_agent_name("story_architect") is None
    assert resolve_agent_name("") is None


def test_chat_reply_defaults_are_empty_intents():
    r = ChatReply(reply_text="hi")
    assert r.thread_intents == [] and r.knowledge_intents == []
    assert r.causal_intents == [] and r.theme_intents == []
