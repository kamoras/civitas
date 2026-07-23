from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
    )

    DATABASE_URL: str = "sqlite:///data/civitas.db"
    DATA_GOV_API_KEY: str = ""
    OLLAMA_BASE_URL: str = "http://ollama:11434"
    OLLAMA_MODEL: str = "LiquidAI/lfm2.5-1.2b-instruct"
    # Optional larger model for the two PUBLIC-facing generation surfaces
    # (full stories, Bluesky posts) — the two-tier design from the
    # 2026-07 permanent-solutions research: those surfaces are low-volume
    # (<=4 stories + a handful of posts per hourly refresh), so a slower
    # 3-4B model is affordable there while the 1.2B default keeps
    # handling the high-volume classification work. Empty = use
    # OLLAMA_MODEL for everything (current behavior). Measured headroom
    # on the production Pi (12GB available): a dense 4B at Q4 (~3GB)
    # fits safely; 30B-class MoE models do not. Enable by pulling the
    # model in ollama and setting e.g. OLLAMA_STORY_MODEL=qwen3:4b —
    # then compare validator rejection rates in the api_cache
    # "action-metrics" tier before/after.
    OLLAMA_STORY_MODEL: str = ""
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
    HOUSE_PTR_RPS: float = 1.0
    SENATE_PTR_RPS: float = 0.5
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
