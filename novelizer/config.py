from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="NOVELIZER_", env_file=".env", extra="ignore")

    db_path: str = "stories/world.db"
    chroma_path: str = "stories/chroma"
    llm_model: str = "llama3.2"
    embed_model: str = "nomic-embed-text"

    # Agent minimum intervals in seconds
    author_interval: int = 300
    continuity_interval: int = 900
    default_interval: int = 120
