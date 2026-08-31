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
    # Vote Smart (api.votesmart.org) — statewide ballot-measure ingestion.
    # Optional: with no key the measure sync is skipped entirely and every
    # state's ballot page renders an explicit "measures not yet ingested"
    # block linking the state's own lookup, rather than an empty section
    # that would read as "this state has no measures".
    VOTESMART_API_KEY: str = ""
    # Google Civic Information API (voterInfoQuery) — town-level ballot
    # content (city council, school board, local measures) that a statewide
    # page structurally can't show, since a real ballot is defined per
    # precinct, not per state. voterInfoQuery is address-keyed, and sending
    # a VISITOR's address off-box is exactly what this platform's
    # architecture exists to prevent — so this is never called with a
    # visitor's address. It's called with a fixed, publicly-known
    # representative address per town (e.g. town hall, see
    # app/data/town_directory.json), chosen by us, not typed by a user.
    # That's a real approximation, not a precinct-accurate lookup: two
    # addresses in the same town can be on different ballots. Optional:
    # with no key, town lookups are skipped and the town selector doesn't
    # appear — the statewide page (VOTESMART_API_KEY's feature) is
    # unaffected either way.
    GOOGLE_CIVIC_API_KEY: str = ""
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
    LLAMA_SERVER_URL: str = "http://llama-server:8070"
    PIPELINE_CACHE_TTL_HOURS: int = 72
    # Skip re-deriving a member whose analysis inputs are byte-identical to
    # the last run's (see analyze/member_fingerprint.py). Defaults OFF: the
    # saving is real but unproven against production data, and the failure
    # mode of a too-coarse fingerprint is a stale scorecard. Turn it on
    # deliberately, after a run with per-phase timings confirms where the
    # time actually goes, and compare a skipped member's scorecard against
    # a forced full re-derivation before trusting it.
    PIPELINE_INCREMENTAL_ANALYSIS: bool = False
    # Let the LLM rephrase a Q&A answer as prose. Off by default, and even
    # when on the rewrite is discarded unless it preserves every figure
    # exactly (services/qa.py::_numbers_are_preserved). Retrieval always
    # produces the answer; this only ever changes how it reads.
    QA_LLM_PHRASING: bool = False
    # Runs a second, experimental issue-generation path (deconstruct each
    # cluster into source-attributed claims first, then generate from that
    # structured set instead of raw article text — see
    # action_center._run_claim_extraction_shadow) alongside the real one,
    # for every candidate cluster on every run. Never publishes anything;
    # only logs an automated grounding-quality comparison via
    # action_metrics, so enough real data accumulates to decide whether to
    # promote it without needing a person to review samples. On by default
    # because that data can only accumulate while it runs — but it costs
    # two extra LLM calls per candidate cluster (small on the Pi's
    # MAX_ISSUES=2 quota, real if that quota ever grows). Turn off once a
    # promotion decision is made either way, to reclaim that headroom.
    ACTION_CENTER_CLAIM_EXTRACTION_SHADOW: bool = True
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
