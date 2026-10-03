"""Calibrate how many votes a congress-specific roll-call position needs.

Regenerates app/data/position_confidence.json, read by score_calculator's
_position_full_confidence_votes(): the vote count at which Constituent
Alignment's position-congruence component stops shrinking toward 50, and
below which fetch/voteview.py leaves a member out of the seat fit and the
saturation scale.

Voteview's Nokken-Poole position is estimated from one congress's roll
calls, so a member with few of them (sworn in late, left early, or anyone
early in a congress) gets a noisy estimate. Measured against the same
member's career DW-NOMINATE position, which pools every congress they
served, the squared gap between the two is the member's real movement in
that congress plus estimation noise that falls with the vote count:

    (nokken_poole_dim1 - nominate_dim1)^2 = drift + k / votes

fitted by least squares over every member of every completed Congress in
CONGRESSES. The full-confidence count is k / drift: the vote count at
which the noise variance equals the real congress-to-congress movement, so
past it the position says more about the member than about the sample.
docs/research/constituent-alignment.md section 14 has the measurement.

Run from the repo (network required):
    python3 backend/scripts/calibrate_position_confidence.py [output.json]
"""

import csv
import datetime
import io
import json
import pathlib
import sys
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app.contact import BOT_USER_AGENT  # noqa: E402

MEMBERS_URL = "https://voteview.com/static/data/out/members/{chamber}{congress}_members.csv"
# Completed Congresses only: a sitting Congress's counts are still growing.
CONGRESSES = range(101, 119)
OUT = pathlib.Path(__file__).resolve().parent.parent / "app" / "data" / "position_confidence.json"


def member_rows(chamber: str, congress: int, cache: pathlib.Path | None = None) -> list[dict]:
    name = f"{chamber}{congress}_members.csv"
    if cache is not None and (cache / name).exists():
        text = (cache / name).read_text()
    else:
        req = urllib.request.Request(
            MEMBERS_URL.format(chamber=chamber, congress=congress), headers={"User-Agent": BOT_USER_AGENT},
        )
        with urllib.request.urlopen(req) as resp:
            text = resp.read().decode()
        if cache is not None:
            cache.mkdir(parents=True, exist_ok=True)
            (cache / name).write_text(text)
    return [r for r in csv.DictReader(io.StringIO(text)) if r.get("chamber") != "President"]


def observations(rows: list[dict]) -> list[tuple[float, float]]:
    """(votes, squared Nokken-Poole minus DW-NOMINATE gap) per member with
    both positions and at least one vote."""
    out = []
    for r in rows:
        try:
            n = float(r["nominate_number_of_votes"])
            gap = float(r["nokken_poole_dim1"]) - float(r["nominate_dim1"])
        except (KeyError, TypeError, ValueError):
            continue
        if n > 0:
            out.append((n, gap * gap))
    return out


def fit_noise(obs: list[tuple[float, float]]) -> dict:
    """Least squares of gap^2 = drift + k / votes; the full-confidence count
    is k / drift."""
    xs = [1.0 / n for n, _ in obs]
    ys = [g for _, g in obs]
    m = len(obs)
    mx, my = sum(xs) / m, sum(ys) / m
    k = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sum((x - mx) ** 2 for x in xs)
    drift = my - k * mx
    return {"k": k, "drift": drift, "full_confidence_votes": k / drift, "members": m}


def calibrate(cache: pathlib.Path | None = None) -> dict:
    obs = []
    for chamber in ("S", "H"):
        for congress in CONGRESSES:
            obs += observations(member_rows(chamber, congress, cache))
    return fit_noise(obs)


def main() -> None:
    out = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else OUT
    fit = calibrate()
    if not (fit["k"] > 0 and fit["drift"] > 0):
        sys.exit(f"implausible fit, not written: {fit}")
    data = {
        "_source": (
            f"Voteview member exports (voteview.com, Lewis et al.), Senate and House, "
            f"Congresses {CONGRESSES.start}-{CONGRESSES.stop - 1}, retrieved "
            f"{datetime.date.today().isoformat()}; regenerate with "
            "backend/scripts/calibrate_position_confidence.py"
        ),
        "_method": (
            "Least squares of (nokken_poole_dim1 - nominate_dim1)^2 = drift + k / "
            "nominate_number_of_votes over every member; full_confidence_votes = k / drift, "
            "the vote count at which a congress-specific position's estimation noise equals "
            "real congress-to-congress movement"
        ),
        "members": fit["members"],
        "k": round(fit["k"], 4),
        "drift": round(fit["drift"], 6),
        "full_confidence_votes": round(fit["full_confidence_votes"]),
    }
    out.write_text(json.dumps(data, indent=1) + "\n")
    print(f"wrote {out}: {data['full_confidence_votes']} votes (k={data['k']}, drift={data['drift']}, "
          f"{data['members']} members)")


if __name__ == "__main__":
    main()
