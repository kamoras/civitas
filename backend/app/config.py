import datetime

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _default_current_congress() -> int:
    """The Congress in session given the wall clock, computed rather than
    hardcoded so this never needs a manual bump after Jan 3 of an odd year
    (previously a hardcoded literal that could only be caught by a separate
    staleness alert an unattended operator might never see — see
    ops_alerts.check_current_congress_staleness, kept as a defensive check
    for the rare case an operator pins this via env for archived-DB
    reproducibility and that pin itself goes stale).

    Mirrors app.pipeline.fetch.congress.congress_for_year's formula inline
    to avoid importing pipeline code at settings-module load time; off by
    one for the ~2 days before Jan 3 convenes in an odd January, same as
    that function.
    """
    return 1 + (datetime.date.today().year - 1789) // 2


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
    #
    # INERT ON THE llama-server BACKEND (the default, and what production
    # runs). call_llm resolves this into use_model, but the llama-server
    # branch calls _call_llama_server(), which takes no model argument and
    # sends no model field — llama-server serves whichever single model it
    # was launched with. Only _call_ollama() honors it. use_model does
    # still feed _make_input_hash, so setting this under llama-server
    # invalidates cached generations and triggers fresh ones from the SAME
    # 1.2B model: the before/after comparison suggested above would show
    # movement from cache churn and resampling, not from a better model.
    # Making the two-tier design real on this backend needs a second
    # llama-server instance (or a swapping proxy) plus a per-call backend
    # target, not just plumbing the argument through.
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
    PRESIDENT_PTR_RPS: float = 0.5
    CURRENT_CONGRESS: int = Field(default_factory=_default_current_congress)
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
