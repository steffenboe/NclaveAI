from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    llm_base_url: str = "https://ai.exxeta.info"
    llm_api_key: str = ""
    llm_model: str = "gpt-4.1"

    agent_roles: str = "INFRA_OPERATOR"
    max_iterations: int = 10

    policy_path: Path


settings = Settings()
