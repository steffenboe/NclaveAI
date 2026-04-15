from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    llm_base_url: str = "https://ai.exxeta.info"
    llm_api_key: str = ""
    llm_model: str = "gpt-4.1"
    available_models: list[str] = [
        "gpt-4.1",
        "gpt-4o",
        "gpt-3.5-turbo",
        "claude-3-opus",
        "claude-3-sonnet",
    ]

    max_iterations: int = 10

    command_timeout_seconds: int = 30

    policy_path: Path

    skills_file: Path = Path("./skills.json")

    runs_file: Path = Path("./runs.json")

    settings_file: Path = Path("./settings.json")


settings = Settings()
