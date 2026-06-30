"""Sponsorship analysis: leadership scores (PageRank) and ideology scores (SVD).

Adapted from GovTrack's methodology (Tauberer 2012, "Observing the
Unobservables in the U.S. Congress").  Both analyses operate on a
senator-senator cosponsorship matrix derived from congressional bill data.

Matrix construction:
    P[sponsor_row][cosponsor_row] += 1 for each cosponsorship event.
    Diagonal starts as identity (each senator "sponsors" their own bills).
    Cell values are square-rooted to flatten outliers (Tauberer 2012).

Leadership (PageRank, Brin & Page 1998):
    Columns are normalized to form a Markov transition matrix.  The
    stationary distribution (computed via power iteration) gives a
    leadership score: senators whose bills attract cosponsors from other
    influential senators rank higher.

Ideology (SVD/PCA, Poole & Rosenthal 1985):
    The second right-singular vector of the cosponsorship matrix captures
    the dominant ideological dimension.  This is oriented so that
    Republicans have positive values (right) and Democrats have negative
    values (left).  The score is blind to party labels; ideology emerges
    purely from behavioral patterns.
"""

import logging
import math

import numpy as np

logger = logging.getLogger(__name__)


def _build_cosponsorship_matrix(
    bills_data: list[dict],
    cosponsors_map: dict[str, list[dict]],
    senator_bioguide_ids: set[str],
) -> tuple[dict[str, int], int, np.ndarray]:
    """Build the senator-senator cosponsorship matrix.

    Args:
        bills_data: List of bill dicts, each with "billId" and
            "sponsorBioguide".
        cosponsors_map: bill_id → list of cosponsor dicts (each has
            "bioguideId").
        senator_bioguide_ids: Set of bioguide IDs for senators in the
            current cohort.

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

    cells: list[tuple[int, int]] = []
    for bill in bills_data:
        sponsor_bio = bill.get("sponsorBioguide")
        if not sponsor_bio or sponsor_bio not in senator_bioguide_ids:
            continue
        sponsor_idx = row(sponsor_bio)
        bill_id = bill.get("billId", "")
        for cosponsor in cosponsors_map.get(bill_id, []):
            cosponsor_bio = cosponsor.get("bioguideId", "")
            if not cosponsor_bio or cosponsor_bio not in senator_bioguide_ids:
                continue
            if cosponsor_bio == sponsor_bio:
                continue
            cosponsor_idx = row(cosponsor_bio)
            cells.append((sponsor_idx, cosponsor_idx))

    n = len(id_to_row)
    P = np.identity(n, dtype=float)
    for sponsor_idx, cosponsor_idx in cells:
        P[sponsor_idx, cosponsor_idx] += 1.0

    for i in range(n):
        for j in range(n):
            P[i, j] = math.sqrt(P[i, j])

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
