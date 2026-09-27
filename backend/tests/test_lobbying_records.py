"""Bill numbers in LDA filing text, and the title check that decides whether
a named number is the bill the member voted on (analyze/lobbying_records)."""

from app.pipeline.analyze.lobbying_records import (
    BILL_TITLE_MATCH_MIN,
    TitlePool,
    bill_mentions,
    congress_of_year,
    names_bill,
    title_match_score,
)


def _ids(text):
    return [bid for bid, _, _ in bill_mentions(text)]


class TestBillMentions:
    def test_record_and_filer_spellings(self):
        # Every spelling below is copied from a 2025 LDA filing.
        assert _ids("H.R. 1492, to equalize") == ["HR.1492"]
        assert _ids("S.1040, Drug Competition Enhancement Act") == ["S.1040"]
        assert _ids("HR 1968 - Full-Year Continuing") == ["HR.1968"]
        assert _ids("S 933 - NASA Transition Authorization") == ["S.933"]
        assert _ids("S.470/HR1078 Respect for State Housing") == ["S.470", "HR.1078"]
        assert _ids("H.Con.Res. 14 and S.Con.Res. 7") == ["HCONRES.14", "SCONRES.7"]
        assert _ids("S. Con. Res. 7") == ["SCONRES.7"]
        assert _ids("H.J.Res. 87") == ["HJRES.87"]

    def test_not_a_bill(self):
        assert _ids("Section 5 of the U.S. 2025 tariff schedule") == []
        assert _ids("items 5 and 6") == []
        assert _ids("P.L. 117-169") == []

    def test_each_side_stops_at_the_neighbouring_number(self):
        (_, b1, a1), (_, b2, a2), _ = bill_mentions(
            "S 526 - Pharmacy Benefit Manager Transparency Act of 2025 "
            "S 527 - Prescription Pricing for the People Act of 2025 "
            "S 832 - EPIC Act of 2025"
        )
        assert "Pharmacy Benefit" in a1 and "Prescription" not in a1
        # S 527's own title is after it; S 526's is on the other side.
        assert "Prescription Pricing" in a2 and "EPIC" not in a2
        assert "Pharmacy Benefit" in b2

    def test_each_side_stops_at_the_clause(self):
        (_, before, after), = bill_mentions(
            "Drug pricing. Issues related to prescription drug value, including H.R. 1492, "
            "to equalize the negotiation period; and the Inflation Reduction Act"
        )
        assert before.strip() == "Issues related to prescription drug value, including"
        assert "Inflation" not in after

    def test_an_initial_does_not_end_a_clause(self):
        (_, before, _), = bill_mentions("Richard L. Trumka Protecting the Right to Organize Act (H.R. 20)")
        assert "Richard L. Trumka" in before


class TestTitleMatch:
    def test_the_filers_title_matches(self):
        _, _, ctx = bill_mentions("S 526 - Pharmacy Benefit Manager Transparency Act of 2025")[0]
        assert title_match_score(ctx, ["Pharmacy Benefit Manager Transparency Act of 2025"]) == 1.0

    def test_a_different_bill_with_the_same_number_does_not(self):
        # A 2025 filing citing the 118th Congress's H.R. 8774 (FY2025 defense
        # appropriations) against the 119th's H.R. 8774.
        _, _, ctx = bill_mentions("S 4921/HR 8774 - Department of Defense Appropriations Act, 2025")[1]
        assert title_match_score(ctx, ["Farm Workforce Modernization Act"]) < BILL_TITLE_MATCH_MIN

    def test_filers_typos_still_match(self):
        _, _, ctx = bill_mentions("S.127 Whole Homes Repairs Act")[0]
        assert title_match_score(ctx, ["Whole-Home Repairs Act of 2025"]) >= BILL_TITLE_MATCH_MIN

    def test_boilerplate_words_alone_do_not_match(self):
        # "Act of 2025" is in thousands of titles; sharing only that is not a match.
        _, _, ctx = bill_mentions("H.R. 2013 - Some Other Act of 2025")[0]
        assert title_match_score(ctx, ["Higher Wages for American Workers Act of 2025"]) < BILL_TITLE_MATCH_MIN

    def test_best_of_several_titles(self):
        _, _, ctx = bill_mentions("H.R. 1, One Big Beautiful Bill Act")[0]
        titles = [
            "To provide for reconciliation pursuant to title II of H. Con. Res. 14.",
            "One Big Beautiful Bill Act",
        ]
        assert title_match_score(ctx, titles) == 1.0

    def test_no_titles(self):
        assert title_match_score("anything", []) == 0.0


def test_congress_of_year():
    assert congress_of_year(2025) == 119
    assert congress_of_year(2026) == 119
    assert congress_of_year(2027) == 120
    assert congress_of_year(1789) == 1


class TestNamesBill:
    POOL = TitlePool({
        "HR.4016": ["Department of Defense Appropriations Act, 2026"],
        "S.2587": ["Department of Education Appropriations Act, 2026"],
        "HR.20": ["Richard L. Trumka Protecting the Right to Organize Act of 2025"],
    })

    def test_sibling_title_loses_to_the_bill_it_names(self):
        assert not names_bill("", " Department of Defense Appropriations Act, 2026",
                              self.POOL._titles["S.2587"], "S.2587", self.POOL)
        assert names_bill("", " Department of Defense Appropriations Act, 2026",
                          self.POOL._titles["HR.4016"], "HR.4016", self.POOL)

    def test_title_before_the_number_counts(self):
        assert names_bill("Richard L. Trumka Protecting the Right to Organize Act of 2025 (", ")",
                          self.POOL._titles["HR.20"], "HR.20", self.POOL)

    def test_a_reintroduced_bill_is_told_apart_by_its_year(self):
        previous = ["Richard L. Trumka Protecting the Right to Organize Act of 2023"]
        assert names_bill("", " - Richard L. Trumka Protecting the Right to Organize Act of 2025",
                          self.POOL._titles["HR.20"], "HR.20", self.POOL, previous)
        assert not names_bill("", " - Richard L. Trumka Protecting the Right to Organize Act of 2023",
                              self.POOL._titles["HR.20"], "HR.20", self.POOL, previous)
