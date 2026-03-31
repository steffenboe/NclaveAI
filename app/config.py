from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    llm_base_url: str = "https://ai.exxeta.info"
    llm_api_key: str = ""
    llm_model: str = "gpt-4.1"

    max_iterations: int = 10

    command_timeout_seconds: int = 30

    policy_path: Path

    skills_file: Path = Path("./skills.json")

    runs_file: Path = Path("./runs.json")


settings = Settings()
