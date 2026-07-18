from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="NOVELIZER_", env_file=".env", extra="ignore")

    # Storage
    db_path: str = "stories/world.db"
    chroma_path: str = "stories/chroma"   # reserved for M1 embeddings
    embed_model: str = "nomic-embed-text"  # reserved for M1 embeddings

    # OpenAI-compatible LLM endpoint
    llm_base_url: str = "http://localhost:8080/v1"
    llm_api_key: str = "not-needed"
    author_model: str = "local-model"
    author_temperature: float = 0.8
    agent_model: str = "local-model"
    agent_temperature: float = 0.7

    # Cadence (seconds)
    author_interval: int = 300
    default_agent_interval: int = 120
    continuity_interval: int = 900
    projector_interval: float = 0.5
