import datetime
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Literal

from pydantic import Field, PrivateAttr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.time_utils import congress_in_session


def _default_current_congress(now: datetime.datetime | None = None) -> int:
    """The Congress in office when the process starts, computed rather than
    hardcoded so this never needs a manual bump after Jan 3 of an odd year
    (previously a hardcoded literal that could only be caught by a separate
    staleness alert an unattended operator might never see — see
    ops_alerts.check_current_congress_staleness, kept for the case an
    operator pins this via env for archived-DB reproducibility and that pin
    itself goes stale).

    Noon ET on Jan 3 of an odd year (the 20th Amendment's hand-over,
    app.time_utils.congress_in_session), the same rule the district lines
    follow: a process started between midnight and noon that day must not
    score the new Congress's windows on the outgoing Congress's lines. It
    is only the starting value — the pipeline process moves it forward at
    the start of each run (scoring_congress), so a process that was already
    running when a new Congress convened is not stuck on the old one.
    """
    return congress_in_session(now)


# The Congress one background job holds (scoring_congress, taken by
# app.background.start_writer and writing()): while set, every read of
# settings.CURRENT_CONGRESS in that job's context answers it, whatever
# another job in the same process advances meanwhile. The context reaches
# asyncio tasks, asyncio.to_thread and contextvars.copy_context().run — NOT
# a plain threading.Thread, loop.run_in_executor or
# ThreadPoolExecutor.submit, whose work reads the process-wide value: hand
# work that reads the setting to a thread through asyncio.to_thread.
_RUN_CONGRESS: ContextVar[int | None] = ContextVar("scoring_congress", default=None)


def advance_current_congress() -> int:
    """Bring settings.CURRENT_CONGRESS up to the Congress in office (noon ET
    on Jan 3 of an odd year), unless an operator pinned it in the
    environment. Never moves it back. Returns the value in effect in this
    context. Called at the start of every background job (via
    scoring_congress, which app.background.start_writer and writing()
    take) and by the API process periodically; a job's own hold is
    unaffected by it."""
    if not settings.current_congress_pinned:
        now = congress_in_session()
        # The process-wide value, not this context's hold (which the
        # attribute read would answer).
        if now > settings.__dict__["CURRENT_CONGRESS"]:
            settings.CURRENT_CONGRESS = now
    return settings.CURRENT_CONGRESS


@contextmanager
def scoring_congress() -> Iterator[int]:
    """Hold ONE Congress for a background job (app.background.start_writer
    and writing() take it for every job): the scored windows (roll-call
    sessions, bills, Voteview ideal points — every read of
    settings.CURRENT_CONGRESS) and House members' district lines
    (fetch/district_pvi reads the same value) come from it for the whole
    run. On entry the process's value is advanced to the Congress in office
    (advance_current_congress), so the first run after noon ET on Jan 3
    moves windows and lines together, with no restart. Inside an enclosing
    hold, keeps that one. Yields the Congress held."""
    held = _RUN_CONGRESS.get()
    if held is not None:
        yield held
        return
    congress = advance_current_congress()
    token = _RUN_CONGRESS.set(congress)
    try:
        yield congress
    finally:
        _RUN_CONGRESS.reset(token)


# Settings removed from the code that a deployed .env may still set. The
# Vote Smart ballot-measure integration was removed in 2026-09: measures
# are read only from each state's own office now.
RETIRED_SETTINGS = frozenset({"VOTESMART_API_KEY"})


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
    )

    DATABASE_URL: str = "sqlite:///data/civitas.db"
    DATA_GOV_API_KEY: str = ""
    # Bureau of Labor Statistics v2 registration key (optional): lifts the
    # public API's 25 requests a day to 500. Unset, the jobs data is read
    # without one.
    BLS_API_KEY: str = ""
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
    # appear — the statewide page (ballot measures read from each state's
    # own office) is unaffected either way.
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
    PIPELINE_LOG_LEVEL: str = "info"
    PIPELINE_CRON_SCHEDULE: str = "0 3 * * *"
    # Which half of the backend this process runs (app.background's
    # writers_allowed, main.lifespan):
    #   all    — both, in one process: local dev, plain `docker compose up`
    #   api    — public reads only; no scheduler, no startup jobs, and any
    #            attempt to start a background writer is refused (503)
    #   worker — the scheduler, startup jobs and every triggered run; nginx
    #            sends it /api/admin/ and the pipeline-trigger endpoints
    # Production (docker-compose.swarm.yml) runs one of each, so a pipeline
    # can't hold the Python interpreter lock, or the container's memory,
    # that page requests need.
    PROCESS_ROLE: Literal["all", "api", "worker"] = "all"
    PIPELINE_TRIGGER_TOKEN: str = ""
    ADMIN_TOKEN: str = ""
    CORS_ORIGINS: str = ""
    CONGRESS_RPS: float = 1.2
    FEC_RPS: float = 0.25
    # Optional free key for the Lobbying Disclosure Act API (lda.gov): a
    # higher rate limit than ~15 requests/minute anonymous. See fetch/lda.py.
    LDA_API_KEY: str | None = None
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
    # How long a nightly pipeline may go with no SUCCESSFUL completion
    # before check_pipeline_staleness() calls it stale. Two days, not one:
    # a single missed night is survivable and self-heals, so alerting at
    # 1d would cry wolf on every transient blip. See that check's own
    # docstring for why this gap needed its own watchdog at all.
    PIPELINE_STALE_ALERT_DAYS: float = 2.0

    # Whether CURRENT_CONGRESS came from the environment (an operator's pin)
    # rather than the clock default — recorded once, at construction, since
    # model_fields_set also grows when code later assigns the field.
    _current_congress_pinned: bool = PrivateAttr(default=False)

    def model_post_init(self, context) -> None:
        super().model_post_init(context)
        self._current_congress_pinned = "CURRENT_CONGRESS" in self.model_fields_set

    @property
    def current_congress_pinned(self) -> bool:
        return self._current_congress_pinned

    def __getattribute__(self, name):
        # A pipeline run's held Congress (scoring_congress) answers for
        # CURRENT_CONGRESS in that run's context.
        if name == "CURRENT_CONGRESS":
            held = _RUN_CONGRESS.get()
            if held is not None:
                return held
        return super().__getattribute__(name)

    @model_validator(mode="before")
    @classmethod
    def _drop_retired_settings(cls, data):
        """Ignore settings this code no longer has. Settings forbids unknown
        keys (a typo'd name fails loudly at startup, which is the point),
        so a key simply deleted here would take the whole app down on any
        deploy whose hand-edited .env still sets it — the Pi's .env is
        edited by hand, not synced. Keys retired on purpose are dropped
        instead; everything else unknown still fails."""
        if isinstance(data, dict):
            data = {k: v for k, v in data.items() if str(k).upper() not in RETIRED_SETTINGS}
        return data


settings = Settings()
