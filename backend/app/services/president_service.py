"""President service — querying and score calculation.

President rows (identity fields included — name/party/term dates/number)
are created and kept current entirely by run_president_pipeline's live
UCSB roster fetch (president_pipeline.py's `_sync_roster`); this module
has no seed data of its own anymore. There is no narrative summary/
key-achievements/key-failures field either — that was hand-written
editorial text with no live source, same problem class as the scored
dimensions this platform removed (below), so it was dropped rather than
kept as unscored "informational" text (2026-07).

Every SCORED dimension
(Public Mandate, Effectiveness, Competence, Agency Alignment) is computed
entirely by run_president_pipeline (president_pipeline.py) from real
fetched data, never seeded:
  - Public Mandate: live approval polling (Truman-33 onward, UCSB
    American Presidency Project — Gallup's own tracking ended Feb 2026)
    or, for earlier presidents, historical election-margin data (also
    UCSB) — see app.pipeline.fetch.presidential_approval/
    presidential_elections.
  - Effectiveness: GDP growth from BEA/FRED (modern era) or
    MeasuringWorth's 1790-present historical series (earlier
    presidents) + BLS jobs data (1939 onward) — see
    app.pipeline.fetch.economic_data/historical_gdp.
  - Competence: executive-order activity rate from UCSB's own EO
    statistics table, covering the full presidency — see
    app.pipeline.fetch.historical_executive_orders. Court-success-rate
    and cabinet-turnover-rate have no live source yet (see
    president_scorer.py's module docstring) and simply don't
    contribute, rather than being backed by a guess.
  - Agency Alignment: Federal Register rulemaking data, Clinton onward
    only — the regulatory record-keeping mechanism this dimension
    measures didn't exist before Federal Register itself (1936).

A dimension's score is None, never a fabricated number or a neutral
default, for any president it's genuinely inapplicable to (see each
_core function in president_scorer.py and compute_president_overall_
score's per-president renormalization).

Independence and Follow-Through were removed entirely (2026-07): both
were always 100% hand-set per-president values with no live formula and
no realistic path to one — Independence's obvious source (OpenSecrets'
revolving-door API) was discontinued in 2025; Follow-Through would need
the same platform-text-vs-action embedding match already proven
unworkable for senators' Promise Persistence (v6.0, config_definitions.
py, after 4 failed attempts). Same precedent: remove rather than keep
presenting a hand-set number as a computed score. See
PRESIDENT_SCORE_WEIGHTS (config_definitions.py) for the redistribution.
"""

import logging

from sqlalchemy.orm import Session

from app.models import President
from app.pipeline.analyze.president_scorer import compute_president_overall_score
from app.schemas import (
    PresidentialScoreSchema,
    PresidentLeaderboardEntry,
    PresidentSchema,
)

logger = logging.getLogger(__name__)


def _build_response(p: President) -> PresidentSchema:
    return PresidentSchema(
        id=p.id,
        name=p.name,
        party=p.party,
        number=p.number,
        term_start=p.term_start,
        term_end=p.term_end,
        is_current=p.is_current,
        score=PresidentialScoreSchema(
            public_mandate=p.score_public_mandate,
            effectiveness=p.score_effectiveness,
            competence=p.score_competence,
            agency_alignment=p.score_agency_alignment,
            overall=compute_president_overall_score(p),
        ),
        avg_approval=p.avg_approval,
        gdp_growth_avg=p.gdp_growth_avg,
        jobs_created_millions=p.jobs_created_millions,
        eo_count=p.eo_count,
        eo_court_success_pct=p.eo_court_success_pct,
        cabinet_turnover_pct=p.cabinet_turnover_pct,
        election_margin=p.election_margin,
    )


def get_president(db: Session, president_id: str) -> PresidentSchema | None:
    p = db.query(President).filter(President.id == president_id).first()
    if not p:
        return None
    return _build_response(p)


def get_president_score_breakdown(db: Session, president_id: str) -> dict | None:
    """Recompute a president's full score-derivation breakdown on-demand,
    directly from whatever live/historical data is currently stored
    (gdp_growth_adjusted, rulemaking_count, election_margin, etc. —
    persisted by president_pipeline.py specifically so this recompute is
    possible without a live re-fetch).

    2026-07: no more DYNAMIC_PRESIDENTS/ECONOMICS_ONLY_PRESIDENTS gating
    and no more seedOnly fallback — every _core function now takes
    whatever's actually stored and returns score=None on its own when a
    dimension is genuinely inapplicable for this president (see each
    _core function's docstring in president_scorer.py). This function
    used to need cohort membership to distinguish "not fetched" from
    "fetched as null" before deciding whether to trust stored data as
    live; that distinction doesn't exist anymore because there's no
    seed-fallback state left to accidentally present as live.
    """
    from app.pipeline.analyze.president_scorer import (
        _agency_alignment_core,
        _competence_core,
        _effectiveness_core,
        _public_mandate_core,
    )
    from app.pipeline.president_pipeline import _term_years

    p = db.query(President).filter(President.id == president_id).first()
    if not p:
        return None

    term_years = _term_years(p.term_start, p.term_end)

    return {
        "publicMandate": _public_mandate_core(
            avg_approval=p.avg_approval,
            approval_trend=p.approval_trend,
            election_margin=p.election_margin,
        ),
        "competence": _competence_core(
            eo_count=p.eo_count,
            eo_court_success_pct=p.eo_court_success_pct,
            cabinet_turnover_pct=p.cabinet_turnover_pct,
            term_years=term_years,
            term_start_year=int(p.term_start[:4]),
        ),
        "effectiveness": _effectiveness_core(
            jobs_created_millions=p.jobs_created_millions,
            gdp_growth_avg=p.gdp_growth_avg,
            term_years=term_years,
            gdp_growth_adjusted=p.gdp_growth_adjusted,
        ),
        "agencyAlignment": _agency_alignment_core(
            rulemaking_count=p.rulemaking_count,
            rulemaking_finalized_pct=p.rulemaking_finalized_pct,
            term_years=term_years,
        ),
    }


def get_all_presidents(db: Session) -> list[PresidentSchema]:
    presidents = db.query(President).order_by(President.number.desc()).all()
    return [_build_response(p) for p in presidents]


def get_president_leaderboard(db: Session) -> list[PresidentLeaderboardEntry]:
    presidents = db.query(President).all()
    entries = []
    for p in presidents:
        score = PresidentialScoreSchema(
            public_mandate=p.score_public_mandate,
            effectiveness=p.score_effectiveness,
            competence=p.score_competence,
            agency_alignment=p.score_agency_alignment,
            overall=compute_president_overall_score(p),
        )
        entries.append(PresidentLeaderboardEntry(
            id=p.id,
            name=p.name,
            party=p.party,
            number=p.number,
            term_start=p.term_start,
            term_end=p.term_end,
            is_current=p.is_current,
            score=score,
            avg_approval=p.avg_approval,
            gdp_growth_avg=p.gdp_growth_avg,
        ))

    entries.sort(key=lambda e: e.score.overall, reverse=True)
    return entries
