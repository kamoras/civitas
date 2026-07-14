from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
    )

    DATABASE_URL: str = "sqlite:///data/civitas.db"
    DATA_GOV_API_KEY: str = ""
    OLLAMA_BASE_URL: str = "http://ollama:11434"
    OLLAMA_MODEL: str = "qwen2.5:1.5b"
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
    # Bluesky integration (leave BSKY_HANDLE empty to disable)
    BSKY_HANDLE: str = ""
    BSKY_APP_PASSWORD: str = ""
    # Site feedback form -> GitHub issue creation (leave empty to disable;
    # the endpoint returns 503 rather than silently dropping submissions).
    # Needs a token scoped to Issues: write on GITHUB_FEEDBACK_REPO only —
    # a fine-grained PAT, not a classic repo-scope token.
    FEEDBACK_TOKEN: str = ""
    GITHUB_FEEDBACK_REPO: str = "kamoras/civitas"
    # Operator alerts (pipeline overruns, skipped runs, ground-truth failures).
    # Always logged + recorded for the admin dashboard; optionally pushed:
    ALERT_NTFY_URL: str = ""    # e.g. https://ntfy.sh/<private-topic>
    PIPELINE_OVERRUN_ALERT_HOURS: float = 8.0


settings = Settings()
