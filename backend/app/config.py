from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
    )

    DATABASE_URL: str = "sqlite:///data/civitas.db"
    DATA_GOV_API_KEY: str = ""
    OLLAMA_BASE_URL: str = "http://ollama:11434"
    OLLAMA_MODEL: str = "deepseek-r1:1.5b"
    LLM_BACKEND: str = "llama-server"
    LLAMA_SERVER_URL: str = "http://host.docker.internal:8070"
    PIPELINE_CACHE_TTL_HOURS: int = 72
    PIPELINE_LOG_LEVEL: str = "info"
    PIPELINE_CRON_SCHEDULE: str = "0 3 * * *"
    PIPELINE_TRIGGER_TOKEN: str = ""
    ADMIN_TOKEN: str = ""
    CORS_ORIGINS: str = ""
    CONGRESS_RPS: float = 1.2
    FEC_RPS: float = 0.25
    GOVINFO_RPS: float = 1.0
    CURRENT_CONGRESS: int = 119
    # Email digest (leave SMTP_HOST empty to disable)
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASS: str = ""
    SMTP_FROM: str = ""
    DIGEST_SECRET: str = "change-me-in-production"  # used for unsubscribe tokens


settings = Settings()
