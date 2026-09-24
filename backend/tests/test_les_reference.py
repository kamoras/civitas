"""Legislative Effectiveness's population reference is measured each run.

Before v6.13 the reference (chamber median credit, average baseline, one
pooled saturation) and the current congress's majority party were Python
literals. Two failures followed:

- Rollover: _SENATE_MAJORITY/_HOUSE_MAJORITY ended at the 119th Congress.
  From the day the 120th convened, every member's majority/minority
  adjustment silently fell back to the flat unknowable-status rate.
- Drift: credit accumulates through a congress, but the median it was
  compared against was frozen on one date — scores crept up all congress,
  then collapsed when the next congress restarted everyone near zero.
"""

import copy
import json
import os
import time

from app.models import President
from app.pipeline.analyze.population_reference import LES_REFERENCE
from app.pipeline.analyze.score_calculator import (
    _advancement_baseline,
    _calc_legislative_effectiveness,
    _les_component_score,
    compute_les_reference,
    derive_chamber_majority,
    load_les_reference,
    write_les_reference,
)
from app.pipeline.senate_pipeline import _live_les_reference, sitting_president_party


def _bills(n, bill_type="s", congress=119, law=0):
    return [
        {"title": f"B{i}", "billType": bill_type, "congress": congress,
         "isLaw": i < law, "latestAction": "Became Public Law" if i < law else "Introduced"}
        for i in range(n)
    ]


class TestDeriveChamberMajority:
    def test_larger_caucus_is_the_majority(self):
        assert derive_chamber_majority(["R"] * 53 + ["D"] * 47, "senate") == "R"
        assert derive_chamber_majority(["D"] * 220 + ["R"] * 215, "house") == "D"

    def test_independents_count_through_their_caucus(self):
        # Caller passes effectiveParty; a raw "I" is ignored, not a vote.
        caucus = ["R"] * 49 + ["D"] * 49 + ["D", "D"]
        assert derive_chamber_majority(caucus, "senate") == "D"
        assert derive_chamber_majority(["R"] * 49 + ["D"] * 49 + ["I", "I"], "senate", "D") == "D"

    def test_senate_tie_goes_to_the_vice_presidents_party(self):
        # The 117th Congress: 50-50, Democratic majority via the VP.
        assert derive_chamber_majority(["R"] * 50 + ["D"] * 50, "senate", "D") == "D"

    def test_tie_without_a_tie_breaker_is_unknowable(self):
        assert derive_chamber_majority(["R"] * 50 + ["D"] * 50, "senate") is None
        assert derive_chamber_majority(["R"] * 217 + ["D"] * 217, "house", "R") is None


class TestCongressRollover:
    def test_table_has_no_entry_for_the_next_congress(self):
        # The failure mode: congress 120 isn't in the historical table.
        assert _advancement_baseline("s", 120, "R") == 0.030
        assert _advancement_baseline("s", 120, "D") == 0.030

    def test_live_majority_restores_the_adjustment(self):
        current = (120, "D")
        assert _advancement_baseline("s", 120, "D", current) > _advancement_baseline("s", 120, "R", current)
        assert _advancement_baseline("hr", 120, "D", current) > _advancement_baseline("hr", 120, "R", current)

    def test_live_majority_only_applies_to_its_own_congress(self):
        assert _advancement_baseline("s", 118, "D", (120, "R")) == _advancement_baseline("s", 118, "D")

    def test_minority_sponsor_is_held_to_a_lower_bar_in_a_new_congress(self, pinned_les_reference):
        ref = copy.deepcopy(pinned_les_reference)
        ref["senate"]["congress"] = 120
        ref["senate"]["majority"] = "D"
        bills = _bills(40, congress=120)
        majority_score = _calc_legislative_effectiveness(bills, None, party="D", les_reference=ref)
        minority_score = _calc_legislative_effectiveness(bills, None, party="R", les_reference=ref)
        assert minority_score > majority_score


class TestComputeReference:
    def test_reference_is_measured_from_the_members_given(self):
        members = [(_bills(n), "R" if n % 2 else "D") for n in range(10, 50)]
        ref = compute_les_reference(members, congress=119, majority="R")
        assert ref["congress"] == 119 and ref["majority"] == "R" and ref["n"] == 40
        assert ref["median_credit"] == 147.5  # median of 10..49 bills x 5 credit
        assert ref["stdev_credit"] > 0 and 0 < ref["avg_baseline"] < 1

    def test_members_without_substantive_bills_are_not_in_the_distribution(self):
        members = [(_bills(10), "D")] * 35 + [([], "R")] * 20 + [(_bills(5, "sres"), "R")] * 5
        assert compute_les_reference(members, 119, "R")["n"] == 35

    def test_too_few_members_yields_no_reference(self):
        # A single-senator filter run, or the first days of a congress.
        assert compute_les_reference([(_bills(10), "D")] * 5, 119, "R") is None

    def test_the_bar_moves_with_the_congress_instead_of_drifting(self, pinned_les_reference):
        """The same member, same record, scores the same whether the chamber
        is early in a congress (everyone has few bills) or late (everyone
        has many) — relative position is what's scored, not calendar time."""
        early = [(_bills(n), "D") for n in range(4, 44)]
        late = [(_bills(3 * n), "D") for n in range(4, 44)]
        ref_early = {"senate": compute_les_reference(early, 119, "R")}
        ref_late = {"senate": compute_les_reference(late, 119, "R")}
        median_early = _bills(24)   # the median member early on
        median_late = _bills(72)    # the median member late
        s_early = _calc_legislative_effectiveness(median_early, None, party="D", les_reference=ref_early)
        s_late = _calc_legislative_effectiveness(median_late, None, party="D", les_reference=ref_late)
        assert s_early == s_late


class TestChamberSpecificSaturation:
    def test_saturation_is_one_and_a_half_of_the_chambers_own_stdev(self, pinned_les_reference):
        ref = copy.deepcopy(pinned_les_reference)
        # Put a House member exactly one House saturation (1.5 x 88.12)
        # above an expected bar of 129: 132.18 extra credit ~= 26 more
        # introduced-only hr bills (5 credit each) than the median member.
        ref["house"]["avg_baseline"] = _advancement_baseline("hr", 119, None)
        bills = _bills(26 + 26, "hr")  # 52 x 5 = 260 ~= 129 + 132
        score, _ = _les_component_score(bills, None, None, ref)
        assert score > 97  # saturated under the House's own spread
        # Under the old pooled saturation (199.87) the same gap was ~2/3.
        ref["house"]["stdev_credit"] = 199.87 / 1.5
        pooled, _ = _les_component_score(bills, None, None, ref)
        assert pooled < 85


class TestReferenceFiles:
    def test_live_file_overrides_bundled_per_chamber(self, pinned_les_reference):
        live = {**pinned_les_reference["senate"], "median_credit": 10.0}
        write_les_reference("senate", live)
        loaded = load_les_reference()
        assert loaded["senate"]["median_credit"] == 10.0
        assert loaded["house"] == pinned_les_reference["house"]  # still bundled

    def test_writes_merge_rather_than_clobber(self, pinned_les_reference):
        write_les_reference("senate", pinned_les_reference["senate"])
        write_les_reference("house", {**pinned_les_reference["house"], "n": 1})
        on_disk = json.loads(open(LES_REFERENCE.live_path).read())
        assert set(on_disk) == {"senate", "house"}
        assert "computed_at" in on_disk["senate"]

    def test_reader_picks_up_another_process_writing_the_file(self, pinned_les_reference):
        # The API worker that didn't run the pipeline must not keep serving
        # the previous run's reference from its cache.
        write_les_reference("senate", pinned_les_reference["senate"])
        assert load_les_reference()["senate"]["median_credit"] == 289.0
        path = LES_REFERENCE.live_path
        data = json.loads(open(path).read())
        data["senate"]["median_credit"] = 7.0
        open(path, "w").write(json.dumps(data))
        later = time.time() + 5
        os.utime(path, (later, later))
        assert load_les_reference()["senate"]["median_credit"] == 7.0

    def test_scoring_without_an_explicit_reference_uses_the_files(self, pinned_les_reference):
        bills = _bills(40)
        explicit = _calc_legislative_effectiveness(bills, None, les_reference=pinned_les_reference)
        assert _calc_legislative_effectiveness(bills, None) == explicit


class TestLiveReferenceInThePipeline:
    def _president(self, db, party):
        db.add(President(
            id="p", name="P", party=party, number=47, term_start="2025-01-20", is_current=True,
        ))
        db.commit()

    def test_measures_persists_and_merges(self, db_session, pinned_les_reference, monkeypatch):
        monkeypatch.setattr("app.pipeline.senate_pipeline.settings.CURRENT_CONGRESS", 120)
        self._president(db_session, "D")
        members = [(_bills(n, congress=120), "R" if n % 2 else "D") for n in range(10, 50)]
        ref = _live_les_reference("senate", members, db_session)
        assert ref["senate"]["congress"] == 120
        assert ref["senate"]["majority"] == "D"  # 20-20 tie -> VP's party
        assert ref["house"] == pinned_les_reference["house"]
        assert load_les_reference()["senate"]["congress"] == 120

    def test_small_run_scores_against_the_last_persisted_reference(self, db_session):
        assert _live_les_reference("senate", [(_bills(10), "D")], db_session) is None

    def test_sitting_president_party(self, db_session):
        assert sitting_president_party(db_session) is None
        self._president(db_session, "R")
        assert sitting_president_party(db_session) == "R"


class TestNewCongressBeforeAReferenceExists:
    def test_scores_neutral_rather_than_against_last_congress(self, pinned_les_reference):
        # Reference still describes the 119th; the member's bills are 120th.
        score, detail = _les_component_score(_bills(3, congress=120), "D", 6.0, pinned_les_reference)
        assert score == 50.0
        assert "120th Congress" in detail
