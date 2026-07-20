"""Sponsorship analysis: leadership scores (PageRank) and ideology scores (SVD).

Adapted from GovTrack's methodology (Tauberer 2012, "Observing the
Unobservables in the U.S. Congress").  Both analyses operate on a
senator-senator cosponsorship matrix derived from congressional bill data.

Matrix construction:
    P[sponsor_row][cosponsor_row] += edge_weight for each cosponsorship
    event (1.0 unless a weight_fn is supplied — see
    _cosponsorship_edge_weight below). Diagonal starts as identity (each
    senator "sponsors" their own bills). Cell values are square-rooted to
    flatten outliers (Tauberer 2012).

Leadership (PageRank, Brin & Page 1998):
    Columns are normalized to form a Markov transition matrix.  The
    stationary distribution (computed via power iteration) gives a
    leadership score: senators whose bills attract cosponsors from other
    influential senators rank higher.

    Cosponsorship-network centrality alone can't tell a substantive bill
    from a message bill introduced purely for the cosponsor list — a
    senator who signs onto ten resolutions with zero chance of passing
    accrues the same network weight as one who cosponsors ten bills that
    actually became law (external critique, 2026-07; see
    ENACTED_EDGE_WEIGHT/ADVANCED_EDGE_WEIGHT/STALLED_EDGE_WEIGHT below for how this is addressed).

Ideology (SVD/PCA, Poole & Rosenthal 1985):
    The second right-singular vector of the cosponsorship matrix captures
    the dominant ideological dimension.  This is oriented so that
    Republicans have positive values (right) and Democrats have negative
    values (left).  The score is blind to party labels; ideology emerges
    purely from behavioral patterns. Unlike Leadership, this intentionally
    does NOT weight edges by bill outcome — a symbolic resolution that
    never advances is often exactly where partisan alignment shows up most
    clearly, so down-weighting it would remove signal rather than noise.
"""

import logging

import numpy as np

from app.pipeline.analyze.score_calculator import _ADVANCEMENT_ACTION_KEYWORDS

logger = logging.getLogger(__name__)

# Cosponsorship-edge weights by bill outcome, replacing the prior flat 1.0
# per cosponsorship. Calibrated 2026-07 against the live Senate/House
# cosponsorship-enrichment corpus (see PR description for the population
# breakdown): most recently-sponsored bills never advance within the
# 2-year window this data is sampled from, so a stalled bill still needs a
# real (non-zero) weight to avoid collapsing the matrix into near-total
# sparsity — it remains genuine evidence of a cosponsorship relationship,
# just weaker evidence of productive collaboration than a bill that
# cleared a real procedural hurdle. Same [floor, 1.0] shape as every other
# calibrated scale in this codebase (e.g. score_calculator.py's
# volume_factor): stalled is not zeroed out, just discounted.
ENACTED_EDGE_WEIGHT = 1.0
ADVANCED_EDGE_WEIGHT = 0.6
STALLED_EDGE_WEIGHT = 0.3


def _cosponsorship_edge_weight(bill: dict) -> float:
    """How much a single cosponsorship of `bill` should count toward
    Legislative Leadership's PageRank — see the weight tiers'
    calibration note. Bills with no isLaw/latestAction data at all (an
    older enrichment path that doesn't fetch outcome data) default to the
    original flat weight rather than being penalized for a data gap."""
    if bill.get("isLaw"):
        return ENACTED_EDGE_WEIGHT
    action = bill.get("latestAction")
    if action is None:
        return ENACTED_EDGE_WEIGHT
    if any(kw in action.lower() for kw in _ADVANCEMENT_ACTION_KEYWORDS):
        return ADVANCED_EDGE_WEIGHT
    return STALLED_EDGE_WEIGHT


def _build_cosponsorship_matrix(
    bills_data: list[dict],
    cosponsors_map: dict[str, list[dict]],
    senator_bioguide_ids: set[str],
    weight_fn=None,
) -> tuple[dict[str, int], int, np.ndarray]:
    """Build the senator-senator cosponsorship matrix.

    Args:
        bills_data: List of bill dicts, each with "billId" and
            "sponsorBioguide".
        cosponsors_map: bill_id → list of cosponsor dicts (each has
            "bioguideId").
        senator_bioguide_ids: Set of bioguide IDs for senators in the
            current cohort.
        weight_fn: optional bill dict -> float, applied per cosponsorship
            edge (defaults to a flat 1.0 for every edge). Leadership/
            PageRank passes _cosponsorship_edge_weight; Ideology/SVD
            intentionally leaves this at the default — see module
            docstring for why.

    Returns:
        (id_to_row, n_senators, P) where id_to_row maps bioguideId to
        matrix index, n_senators is the matrix dimension, and P is the
        N×N cosponsorship matrix.
    """
    id_to_row: dict[str, int] = {}

    def row(bio_id: str) -> int:
        if bio_id not in id_to_row:
            id_to_row[bio_id] = len(id_to_row)
        return id_to_row[bio_id]

    for bio_id in senator_bioguide_ids:
        row(bio_id)

    cells: list[tuple[int, int, float]] = []
    for bill in bills_data:
        sponsor_bio = bill.get("sponsorBioguide")
        if not sponsor_bio or sponsor_bio not in senator_bioguide_ids:
            continue
        sponsor_idx = row(sponsor_bio)
        bill_id = bill.get("billId", "")
        weight = weight_fn(bill) if weight_fn else 1.0
        for cosponsor in cosponsors_map.get(bill_id, []):
            cosponsor_bio = cosponsor.get("bioguideId", "")
            if not cosponsor_bio or cosponsor_bio not in senator_bioguide_ids:
                continue
            if cosponsor_bio == sponsor_bio:
                continue
            cosponsor_idx = row(cosponsor_bio)
            cells.append((sponsor_idx, cosponsor_idx, weight))

    n = len(id_to_row)
    P = np.identity(n, dtype=float)
    for sponsor_idx, cosponsor_idx, weight in cells:
        P[sponsor_idx, cosponsor_idx] += weight

    # Elementwise sqrt (all cells are >= 0: identity diagonal + nonnegative
    # cosponsorship weights) — vectorized instead of an O(n^2) Python loop.
    P = np.sqrt(P)

    return id_to_row, n, P


def _rescale(u: np.ndarray, log_scale: bool = False) -> list[float]:
    """Rescale a vector to [0, 1].

    When log_scale is True, applies a log transform that maps the median
    to 0.5, giving better spread for power-law distributions (typical of
    PageRank output).
    """
    u_min, u_max = float(np.min(u)), float(np.max(u))
    if u_max - u_min < 1e-12:
        return [0.5] * len(u)
    u = (u - u_min) / (u_max - u_min)

    if log_scale:
        m = float(np.median(u))
        denom = 2 * m - 1
        if abs(denom) > 1e-9:
            s = -(m ** 2) / denom
            # When median >= 0.5, s goes negative (log of a negative value is
            # undefined), so we fall back to the linear scale already computed
            # above. The linear scale is still informative; the log transform
            # primarily helps when PageRank is heavily left-skewed (many low
            # scorers), which isn't the case when median >= 0.5.
            if s > 0:
                u = np.log(u + s)
                u_min2, u_max2 = float(np.min(u)), float(np.max(u))
                if u_max2 - u_min2 > 1e-12:
                    u = (u - u_min2) / (u_max2 - u_min2)

    return [float(v) for v in u]


def compute_leadership_scores(
    bills_data: list[dict],
    cosponsors_map: dict[str, list[dict]],
    senator_bioguide_ids: set[str],
    senator_parties: dict[str, str] | None = None,
) -> dict[str, float]:
    """Compute PageRank-based legislative leadership scores.

    Returns a dict mapping bioguideId → leadership score (0.0 to 1.0).
    Higher values indicate senators whose bills attract more cosponsors
    from other influential senators (Brin & Page 1998).
    """
    if len(senator_bioguide_ids) < 5:
        return {}

    id_to_row, n, P = _build_cosponsorship_matrix(
        bills_data, cosponsors_map, senator_bioguide_ids,
        weight_fn=_cosponsorship_edge_weight,
    )
    if n < 5:
        return {}

    P_pr = np.copy(P)
    min_data = 10.0
    for col in range(n):
        s = np.sum(P_pr[:, col])
        if s < min_data:
            P_pr[:, col] += (min_data - s) / n
            s = min_data
        P_pr[:, col] /= s

    damping = 0.85
    v = np.ones((n, 1)) / n
    x = np.ones((n, 1)) / n

    for _ in range(500):
        y = damping * np.dot(P_pr, x)
        w = np.sum(np.abs(x)) - np.sum(np.abs(y))
        y = y + w * v
        if np.sum(np.abs(y - x)) < 1e-10:
            break
        x = y

    scores = _rescale(x.flatten(), log_scale=True)

    row_to_id = {v: k for k, v in id_to_row.items()}
    return {row_to_id[i]: scores[i] for i in range(n) if row_to_id[i] in senator_bioguide_ids}


def compute_ideology_scores(
    bills_data: list[dict],
    cosponsors_map: dict[str, list[dict]],
    senator_bioguide_ids: set[str],
    senator_parties: dict[str, str],
) -> dict[str, float]:
    """Compute SVD-based ideology scores from cosponsorship patterns.

    Returns a dict mapping bioguideId → ideology score (0.0 to 1.0).
    Values near 0.0 indicate left/Democratic alignment, values near 1.0
    indicate right/Republican alignment, derived purely from cosponsorship
    behavior without using party labels as input (Tauberer 2012).
    """
    if len(senator_bioguide_ids) < 10:
        return {}

    id_to_row, n, P = _build_cosponsorship_matrix(
        bills_data, cosponsors_map, senator_bioguide_ids,
    )
    if n < 10:
        return {}

    try:
        _u, _s, vh = np.linalg.svd(P)
    except np.linalg.LinAlgError:
        logger.warning("SVD failed for ideology analysis")
        return {}

    if vh.shape[0] < 2:
        return {}

    spectrum = vh[1, :]

    row_to_id = {v: k for k, v in id_to_row.items()}
    r_scores = [
        spectrum[i]
        for i in range(n)
        if row_to_id.get(i) in senator_parties
        and senator_parties[row_to_id[i]] == "R"
    ]
    if r_scores:
        r_mean = sum(r_scores) / len(r_scores)
        if abs(r_mean) > 1e-9:
            spectrum = spectrum * (r_mean / abs(r_mean))

    scores = _rescale(spectrum)

    return {row_to_id[i]: scores[i] for i in range(n) if row_to_id[i] in senator_bioguide_ids}


def describe_senator_position(
    ideology: float,
    leadership: float,
    party: str,
) -> str:
    """Generate a GovTrack-style description of a senator's position.

    Uses the ideology × leadership grid to produce labels like
    "progressive Democratic leader" or "conservative Republican follower"
    (Tauberer 2012).
    """
    # ideology is already rescaled to [0, 1] with 0 = most-left, 1 = most-
    # right (see compute_ideology_scores), so these are terciles of that
    # scale, not raw SVD output. D/R buckets use a wider 30/70 split (the
    # middle 40% reads as "moderate" for a party member) than Independents'
    # 35/65 split, since Independents have no "centrist" party label to fall
    # back to and a narrower middle band better matches GovTrack's original
    # three-way split for unaffiliated members (Tauberer 2012).
    if party == "D":
        ideo_label = (
            "progressive" if ideology < 0.30
            else "centrist" if ideology > 0.70
            else "moderate"
        )
        party_label = "Democrat"
    elif party == "R":
        ideo_label = (
            "centrist" if ideology < 0.30
            else "conservative" if ideology > 0.70
            else "moderate"
        )
        party_label = "Republican"
    else:
        ideo_label = (
            "left-leaning" if ideology < 0.35
            else "right-leaning" if ideology > 0.65
            else "centrist"
        )
        party_label = "Independent"

    # leadership is the rescaled PageRank score (see compute_leadership_scores),
    # already log-spread across [0, 1] to counter its power-law distribution
    # (most senators cluster low, a few attract disproportionate cosponsor
    # weight) — top/bottom quartile on that spread scale is a meaningfully
    # large gap in raw influence, not just a quartile of a linear scale.
    if leadership > 0.75:
        role = "leader"
    elif leadership < 0.25:
        role = "follower"
    else:
        role = ""

    parts = [ideo_label, party_label]
    if role:
        parts.append(role)
    return " ".join(parts)


def compute_bipartisanship_scores(
    bills_data: list[dict],
    cosponsors_map: dict[str, list[dict]],
    member_parties: dict[str, str],
    min_interactions: int = 5,
) -> dict[str, float]:
    """Cross-party coalition breadth from cosponsorship behavior.

    Modeled on the Lugar Center Bipartisan Index (Lugar Center &
    Georgetown McCourt School, 2014-): a member's willingness to work
    across the aisle is measured on both sides of sponsorship —

      * receiving: of the cosponsors a member attracts to their own
        bills, what share come from the other party; and
      * giving: of the bills a member chooses to cosponsor, what share
        are sponsored by the other party.

    Both directions matter: attracting cross-party support shows bills
    written for a broad constituency, and lending support across the
    aisle shows engagement beyond the member's base (Harbridge 2015,
    "Is Bipartisanship Dead?", Cambridge UP).

    Scores are normalized to the cohort median (median -> 0.5, 2x the
    median or better -> 1.0), which makes the measure symmetric across
    parties and majority status without any fixed constant: the
    normalization is recomputed from the observed cohort every run.
    Members of neither major party are assigned the side they cosponsor
    with most (caucus inference, consistent with normalize_votes);
    members with fewer than ``min_interactions`` observed interactions
    are omitted (callers treat missing as neutral, and the confidence
    grade reflects the thin data).

    Returns bioguideId -> [0, 1].
    """
    def _norm_party(p: str | None) -> str | None:
        if not p:
            return None
        p = p.strip().upper()[:1]
        return p if p in ("D", "R") else None

    sponsor_party_by_bill: dict[str, str] = {}
    sponsor_bio_by_bill: dict[str, str] = {}
    for b in bills_data:
        sp = _norm_party(b.get("sponsorParty"))
        if sp and b.get("billId"):
            sponsor_party_by_bill[b["billId"]] = sp
            if b.get("sponsorBioguide"):
                sponsor_bio_by_bill[b["billId"]] = b["sponsorBioguide"]

    # First pass: per-member cross/total counts on both directions, and
    # D/R giving profile for caucus inference of Independents.
    give_total: dict[str, int] = {}
    give_cross_d: dict[str, int] = {}  # cosponsored a D-sponsored bill
    give_cross_r: dict[str, int] = {}
    recv_total: dict[str, int] = {}
    recv_from_d: dict[str, int] = {}
    recv_from_r: dict[str, int] = {}

    for bill_id, sponsor_party in sponsor_party_by_bill.items():
        sponsor_bio = sponsor_bio_by_bill.get(bill_id)
        for co in cosponsors_map.get(bill_id, []):
            co_bio = co.get("bioguideId", "")
            if not co_bio or co_bio == sponsor_bio:
                continue
            co_party = _norm_party(co.get("party")) or _norm_party(
                member_parties.get(co_bio)
            )
            give_total[co_bio] = give_total.get(co_bio, 0) + 1
            if sponsor_party == "D":
                give_cross_d[co_bio] = give_cross_d.get(co_bio, 0) + 1
            else:
                give_cross_r[co_bio] = give_cross_r.get(co_bio, 0) + 1
            if sponsor_bio:
                recv_total[sponsor_bio] = recv_total.get(sponsor_bio, 0) + 1
                if co_party == "D":
                    recv_from_d[sponsor_bio] = recv_from_d.get(sponsor_bio, 0) + 1
                elif co_party == "R":
                    recv_from_r[sponsor_bio] = recv_from_r.get(sponsor_bio, 0) + 1

    def _side(bio: str) -> str | None:
        p = _norm_party(member_parties.get(bio))
        if p:
            return p
        d, r = give_cross_d.get(bio, 0), give_cross_r.get(bio, 0)
        if d + r == 0:
            return None
        return "D" if d >= r else "R"

    raw_rates: dict[str, float] = {}
    for bio in set(give_total) | set(recv_total):
        side = _side(bio)
        if side is None:
            continue
        cross = 0
        total = 0
        gt = give_total.get(bio, 0)
        if gt:
            cross += give_cross_r.get(bio, 0) if side == "D" else give_cross_d.get(bio, 0)
            total += gt
        rt = recv_total.get(bio, 0)
        if rt:
            cross += recv_from_r.get(bio, 0) if side == "D" else recv_from_d.get(bio, 0)
            total += rt
        if total >= min_interactions:
            raw_rates[bio] = cross / total

    if len(raw_rates) < 10:
        return {}

    rates = sorted(raw_rates.values())
    median = rates[len(rates) // 2]
    if median <= 0:
        # Degenerate cohort (no observed crossing anywhere): fall back to
        # an absolute scale where 30% cross-party interactions = 1.0.
        return {bio: min(r / 0.30, 1.0) for bio, r in raw_rates.items()}

    return {bio: min(r / (2.0 * median), 1.0) for bio, r in raw_rates.items()}
