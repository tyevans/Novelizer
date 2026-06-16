from novelizer.config import Settings


def test_defaults():
    s = Settings()
    assert s.db_path == "stories/world.db"
    assert s.chroma_path == "stories/chroma"
    assert s.llm_model == "llama3.2"
    assert s.embed_model == "nomic-embed-text"
    assert s.author_interval == 300
    assert s.continuity_interval == 900
    assert s.default_interval == 120
