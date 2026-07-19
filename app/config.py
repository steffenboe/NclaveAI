from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    llm_base_url: str = "https://api.openai.com"
    llm_api_key: str = ""
    llm_model: str = "gpt-4.1"

    # Speech-to-Text and Text-to-Speech providers (OpenAI-compatible)
    stt_base_url: str = "https://api.openai.com"
    stt_api_key: str = ""
    stt_model: str = "whisper-1"

    tts_base_url: str = "https://api.openai.com"
    tts_api_key: str = ""
    tts_model: str = "tts-1"
    tts_voice: str = "alloy"

    # SSL/TLS Configuration
    ssl_keyfile: str | None = None
    ssl_certfile: str | None = None

    max_iterations: int = 10

    command_timeout_seconds: int = 30

    skills_file: Path = Path("./skills.json")

    runs_file: Path = Path("./runs.json")

    scheduled_tasks_file: Path = Path("./scheduled_tasks.json")

    settings_file: Path = Path("./settings.json")

    secrets_file: Path = Path("./secrets.json")

    users_file: Path = Path("./users.json")

    teams_file: Path = Path("./teams.json")

    api_keys_file: Path = Path("./api_keys.json")

    audit_file: Path = Path("./audit.jsonl")

    policy_test_file: Path = Path("./policy_test.json")

    jwt_secret: str = "change-me-in-production"

    admin_username: str = "admin"

    admin_password: str = ""

    mongodb_uri: str | None = None

    mongodb_db_name: str = "nclaveai"

    llm_ca_bundle: str | None = None


settings = Settings()
