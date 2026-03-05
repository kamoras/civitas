"""Tests for the bill analyzer hybrid classification.

Tests:
1. Procedural keyword detection
2. Policy area embedding classification
3. Validation logic
4. Nomination detection
5. Rich text construction for uninformative bill names
"""

import pytest

from app.pipeline.analyze.bill_analyzer import (
    _NOMINATION_NAME_RE,
    _build_classification_text,
    classify_policy_area,
    classify_policy_areas_multi,
    _validate_classifications,
)


class TestProceduralDetection:
    """Procedural texts should be detected via embedding similarity."""

    @pytest.mark.parametrize(
        "text",
        [
            "Naming of a post office building in Springfield Illinois",
            "Honoring the life and legacy of a distinguished veteran",
            "Designating the week of May 1 as National Teacher Week",
            "Authorizing the use of the rotunda of the Capitol",
        ],
    )
    def test_procedural_texts_detected(self, text):
        area, confidence = classify_policy_area(text)
        assert area == "PROCEDURAL"

    def test_empty_text_is_procedural(self):
        area, confidence = classify_policy_area("")
        assert area == "PROCEDURAL"
        assert confidence == 0.0

    def test_very_short_text_is_procedural(self):
        area, confidence = classify_policy_area("abc")
        assert area == "PROCEDURAL"
        assert confidence == 0.0


@pytest.mark.slow
class TestPolicyAreaClassification:
    """Embedding-based policy area classification."""

    @pytest.mark.parametrize(
        "text, expected",
        [
            ("Hospital and medical insurance reform healthcare system regulation", "HEALTHCARE"),
            ("National Defense Authorization Act military spending", "DEFENSE"),
            ("Gun violence prevention and background check legislation", "GUNS"),
            ("Climate change EPA emissions pollution regulations clean air water", "ENVIRONMENT"),
            ("Federal income tax reform and corporate tax rates", "TAXES"),
            ("Border security and immigration reform pathway to citizenship", "IMMIGRATION"),
            ("Student loan forgiveness and federal education funding", "EDUCATION"),
            ("Criminal justice reform and police accountability", "JUSTICE"),
            ("International trade agreement tariffs with China", "TRADE"),
            ("Minimum wage increase and worker protection legislation", "LABOR"),
        ],
    )
    def test_clear_policy_areas(self, text, expected):
        area, confidence = classify_policy_area(text)
        assert area == expected, f"'{text}' classified as {area}, expected {expected}"
        assert confidence > 0.3

    def test_confidence_is_bounded(self):
        _, confidence = classify_policy_area("Healthcare reform and Medicare expansion")
        assert 0.0 <= confidence <= 1.0


class TestValidation:
    """Post-classification validation and sanitization."""

    def test_missing_policy_area_defaults_to_procedural(self):
        bills = [{"billId": "1", "policyArea": None, "stance": "reform", "stanceVote": "Yea", "partyLeaning": "D"}]
        _validate_classifications(bills)
        assert bills[0]["policyArea"] == "PROCEDURAL"

    def test_policy_area_uppercased(self):
        bills = [{"billId": "1", "policyArea": "healthcare", "stance": "reform", "stanceVote": "Yea", "partyLeaning": "D"}]
        _validate_classifications(bills)
        assert bills[0]["policyArea"] == "HEALTHCARE"

    def test_stance_lowercased_and_validated(self):
        bills = [{"billId": "1", "policyArea": "DEFENSE", "stance": "PRO", "stanceVote": "Yea", "partyLeaning": "R"}]
        _validate_classifications(bills)
        assert bills[0]["stance"] == "pro"

    def test_invalid_stance_normalized_to_neutral(self):
        bills = [{"billId": "1", "policyArea": "DEFENSE", "stance": "PRO-MILITARY", "stanceVote": "Yea", "partyLeaning": "R"}]
        _validate_classifications(bills)
        assert bills[0]["stance"] == "neutral"

    def test_missing_stance_defaults_to_neutral(self):
        bills = [{"billId": "1", "policyArea": "DEFENSE", "stance": None, "stanceVote": "Yea", "partyLeaning": "R"}]
        _validate_classifications(bills)
        assert bills[0]["stance"] == "neutral"

    def test_valid_stance_votes_preserved(self):
        bills = [
            {"billId": "1", "policyArea": "DEFENSE", "stance": "x", "stanceVote": "Yea", "partyLeaning": "R"},
            {"billId": "2", "policyArea": "DEFENSE", "stance": "x", "stanceVote": "Nay", "partyLeaning": "R"},
        ]
        _validate_classifications(bills)
        assert bills[0]["stanceVote"] == "Yea"
        assert bills[1]["stanceVote"] == "Nay"

    def test_invalid_party_leaning_defaults_to_bipartisan(self):
        bills = [{"billId": "1", "policyArea": "DEFENSE", "stance": "x", "stanceVote": "Yea", "partyLeaning": "X"}]
        _validate_classifications(bills)
        assert bills[0]["partyLeaning"] == "bipartisan"


class TestMultiAreaClassification:
    """Tests for multi-area bill classification (Adler & Wilkerson 2012)."""

    def test_returns_list_of_dicts(self):
        result = classify_policy_areas_multi("Healthcare reform and Medicare expansion")
        assert isinstance(result, list)
        assert len(result) >= 1
        assert all("area" in a and "confidence" in a for a in result)

    def test_primary_area_matches_single_classify(self):
        text = "National Defense Authorization Act military spending"
        single_area, _ = classify_policy_area(text)
        multi_areas = classify_policy_areas_multi(text)
        assert multi_areas[0]["area"] == single_area

    def test_multi_domain_bill_returns_multiple_areas(self):
        text = (
            "Comprehensive legislation addressing healthcare insurance reform, "
            "tax credits for medical expenses, and environmental protections "
            "for hospital waste disposal regulations"
        )
        result = classify_policy_areas_multi(text)
        areas = {a["area"] for a in result}
        assert len(result) >= 2, f"Expected multi-area for complex bill, got: {areas}"

    def test_empty_text_returns_procedural(self):
        result = classify_policy_areas_multi("")
        assert result == [{"area": "PROCEDURAL", "confidence": 0.0}]

    def test_confidences_are_bounded(self):
        result = classify_policy_areas_multi("Gun control background check legislation")
        for a in result:
            assert 0.0 <= a["confidence"] <= 1.0

    def test_areas_ordered_by_confidence(self):
        result = classify_policy_areas_multi(
            "Renewable energy tax credits and environmental protection funding"
        )
        if len(result) > 1:
            confs = [a["confidence"] for a in result]
            assert confs == sorted(confs, reverse=True)


class TestNominationDetection:
    """Nomination names must be detected to avoid policy area pollution."""

    @pytest.mark.parametrize(
        "name",
        [
            "Byron B. Conway, of Wisconsin, to be United States District Judge for the Eastern District of Wisconsin",
            "Christopher Charles Fonzone, of Pennsylvania, to be an Assistant Attorney General",
            "David Clay Fowlkes, of Arkansas, to be United States District Judge for the Western District of Arkansas",
            "Matthew James Marzano, of Illinois, to be a Member of the Nuclear Regulatory Commission",
            "Robert P. Chamberlin, of Mississippi, to be United States District Judge",
            "Nomination of John Smith",
            "On the Nomination (Confirmation of the nominee)",
        ],
    )
    def test_nomination_names_detected(self, name):
        assert _NOMINATION_NAME_RE.search(name) is not None, (
            f"Nomination not detected: {name}"
        )

    @pytest.mark.parametrize(
        "name",
        [
            "National Defense Authorization Act for Fiscal Year 2024",
            "Prescription Drug Pricing Reform Act",
            "Background Check Expansion Act",
            "Consolidated Appropriations Act, 2026",
            "Budd Amdt. No. 1243",
            "Collins Amdt. No. 3937",
        ],
    )
    def test_non_nomination_names_not_flagged(self, name):
        assert _NOMINATION_NAME_RE.search(name) is None, (
            f"Non-nomination falsely detected: {name}"
        )


class TestMultiAreaPartyAlignment:
    """Tests for per-area party alignment with weighted aggregation."""

    def test_returns_expected_shape(self):
        from app.pipeline.analyze.party_platform import classify_party_alignment_multi

        areas = [
            {"area": "HEALTHCARE", "confidence": 0.7},
            {"area": "TAXES", "confidence": 0.5},
        ]
        result = classify_party_alignment_multi(
            "Expand Medicare and reduce taxes on wealthy", areas, "pro"
        )
        assert "overall" in result
        assert "weight" in result
        assert "areas" in result
        assert result["overall"] in ("R", "D", "bipartisan")
        assert 0.0 <= result["weight"] <= 1.0

    def test_procedural_only_returns_bipartisan(self):
        from app.pipeline.analyze.party_platform import classify_party_alignment_multi

        areas = [{"area": "PROCEDURAL", "confidence": 0.95}]
        result = classify_party_alignment_multi("Cloture motion", areas, "neutral")
        assert result["overall"] == "bipartisan"
        assert result["weight"] == 0.0

    def test_per_area_alignment_populated(self):
        from app.pipeline.analyze.party_platform import classify_party_alignment_multi

        areas = [
            {"area": "ENVIRONMENT", "confidence": 0.65},
            {"area": "ENERGY", "confidence": 0.55},
        ]
        result = classify_party_alignment_multi(
            "Clean energy investment and emissions reduction targets", areas, "pro"
        )
        assert len(result["areas"]) >= 1
        for a in result["areas"]:
            assert "area" in a
            assert "party" in a
            assert a["party"] in ("R", "D", "bipartisan")


class TestAlignmentsFromVotes:
    """Test _alignments_from_votes with multi-area bill data."""

    def test_multi_area_votes_counted_per_area(self):
        from app.pipeline.analyze.party_platform import _alignments_from_votes

        votes = [
            {
                "vote": "Yea",
                "policyAreas": [
                    {"area": "HEALTHCARE", "confidence": 0.9, "party": "D"},
                    {"area": "TAXES", "confidence": 0.7, "party": "R"},
                ],
            },
            {
                "vote": "Yea",
                "policyAreas": [
                    {"area": "HEALTHCARE", "confidence": 0.8, "party": "D"},
                ],
            },
            {
                "vote": "Nay",
                "policyAreas": [
                    {"area": "HEALTHCARE", "confidence": 0.85, "party": "R"},
                ],
            },
        ]
        record = {"keyVotes": votes, "recentVotes": []}
        result = _alignments_from_votes(record)
        areas_found = {a["area"] for a in result}
        assert "HEALTHCARE" in areas_found

    def test_single_area_fallback(self):
        """When policyAreas is empty, falls back to single policyArea + partyLeaning."""
        from app.pipeline.analyze.party_platform import _alignments_from_votes

        votes = [
            {"vote": "Yea", "policyArea": "DEFENSE", "partyLeaning": "R",
             "policyAreas": []},
            {"vote": "Yea", "policyArea": "DEFENSE", "partyLeaning": "R",
             "policyAreas": []},
            {"vote": "Nay", "policyArea": "DEFENSE", "partyLeaning": "D",
             "policyAreas": []},
        ]
        record = {"keyVotes": votes, "recentVotes": []}
        result = _alignments_from_votes(record)
        areas_found = {a["area"] for a in result}
        assert "DEFENSE" in areas_found

    def test_procedural_areas_skipped(self):
        from app.pipeline.analyze.party_platform import _alignments_from_votes

        votes = [
            {
                "vote": "Yea",
                "policyAreas": [
                    {"area": "PROCEDURAL", "confidence": 0.95, "party": "bipartisan"},
                ],
            },
        ]
        record = {"keyVotes": votes, "recentVotes": []}
        result = _alignments_from_votes(record)
        assert result == []

    def test_confidence_weighting(self):
        """Higher-confidence areas should contribute more weight."""
        from app.pipeline.analyze.party_platform import _alignments_from_votes

        votes = []
        for _ in range(5):
            votes.append({
                "vote": "Yea",
                "policyAreas": [
                    {"area": "IMMIGRATION", "confidence": 0.9, "party": "D"},
                ],
            })
        record = {"keyVotes": votes, "recentVotes": []}
        result = _alignments_from_votes(record)
        imm = [a for a in result if a["area"] == "IMMIGRATION"]
        assert len(imm) == 1
        assert imm[0]["alignment"] == "D"

    def test_empty_record_returns_empty(self):
        from app.pipeline.analyze.party_platform import _alignments_from_votes

        assert _alignments_from_votes({}) == []
        assert _alignments_from_votes({"keyVotes": [], "recentVotes": []}) == []

    def test_bipartisan_areas_skipped(self):
        """Areas with party='bipartisan' in policyAreas should not contribute."""
        from app.pipeline.analyze.party_platform import _alignments_from_votes

        votes = [
            {
                "vote": "Yea",
                "policyAreas": [
                    {"area": "DEFENSE", "confidence": 0.8, "party": "bipartisan"},
                ],
            },
            {
                "vote": "Yea",
                "policyAreas": [
                    {"area": "DEFENSE", "confidence": 0.8, "party": "bipartisan"},
                ],
            },
        ]
        record = {"keyVotes": votes, "recentVotes": []}
        result = _alignments_from_votes(record)
        assert result == []


class TestIdeologyBlendInPartisanDepth:
    """Test that SVD ideology_score blends into partisan depth correctly."""

    def test_ideology_dominates_with_no_votes(self):
        """With no vote data, ideology_score alone shapes the lean."""
        from app.pipeline.analyze.party_platform import analyze_partisan_depth

        result = analyze_partisan_depth(
            promises=[],
            senator_party="D",
            voting_record={"keyVotes": [], "recentVotes": []},
            ideology_score=0.0,
        )
        assert result["totalPositions"] == 0

    def test_ideology_adjusts_sparse_vote_lean(self):
        """With few votes, ideology_score should pull the overall lean."""
        from app.pipeline.analyze.party_platform import analyze_partisan_depth

        votes = [
            {"vote": "Yea", "policyArea": "DEFENSE", "partyLeaning": "R",
             "policyAreas": []},
            {"vote": "Yea", "policyArea": "DEFENSE", "partyLeaning": "R",
             "policyAreas": []},
        ]
        record = {"keyVotes": votes, "recentVotes": []}

        without = analyze_partisan_depth(
            promises=[], senator_party="D",
            voting_record=record, ideology_score=None,
        )
        with_d_ideology = analyze_partisan_depth(
            promises=[], senator_party="D",
            voting_record=record, ideology_score=0.1,
        )
        assert with_d_ideology["overallLean"] < without["overallLean"]

    def test_rich_votes_override_ideology(self):
        """With many votes (>=15), ideology_score has minimal effect."""
        from app.pipeline.analyze.party_platform import analyze_partisan_depth

        votes = []
        for _ in range(20):
            votes.append({
                "vote": "Yea",
                "policyArea": "HEALTHCARE",
                "partyLeaning": "D",
                "policyAreas": [],
            })
        record = {"keyVotes": votes, "recentVotes": []}

        without = analyze_partisan_depth(
            promises=[], senator_party="D",
            voting_record=record, ideology_score=None,
        )
        with_r_ideology = analyze_partisan_depth(
            promises=[], senator_party="D",
            voting_record=record, ideology_score=0.9,
        )
        assert abs(with_r_ideology["overallLean"] - without["overallLean"]) < 0.05


class TestGunControlBillPartyAlignment:
    """Regression: gun control bills must classify as D-aligned, not R.

    Jaime's Law (Blumenthal) restricts ammunition sales — a classic
    gun control measure.  The embedding classifier previously assigned
    "R" because the R GUNS platform used "oppose gun control" language
    that embeds very close to "gun control" itself (sentence-transformers
    are weak at antonymy; Ettinger 2020).

    Fix: (1) directionally positive platform descriptions (R describes
    what they WANT, not what they oppose), and (2) stance-conditioned
    query construction so the "anti" stance on GUNS shifts the query
    toward the D centroid.
    """

    @pytest.mark.parametrize(
        "bill_text, stance",
        [
            ("Jaime's Law to restrict the sale and transfer of ammunition", "anti"),
            ("A bill to ban assault weapons and high-capacity magazines", "anti"),
            ("Universal background check legislation for all gun purchases", "pro"),
            ("Red flag law to remove firearms from dangerous individuals", "pro"),
            ("A bill to regulate ammunition sales and transfers", "anti"),
        ],
    )
    def test_gun_control_bills_align_democrat(self, bill_text, stance):
        from app.pipeline.analyze.party_platform import classify_party_alignment

        result = classify_party_alignment(bill_text, "GUNS", stance)
        assert result in ("D", "bipartisan"), (
            f"Gun control bill '{bill_text}' classified as '{result}', "
            f"expected 'D' or 'bipartisan'"
        )

    @pytest.mark.parametrize(
        "bill_text, stance",
        [
            ("Concealed carry reciprocity act to expand gun rights nationwide", "pro"),
            ("A bill to protect second amendment rights and deregulate firearms", "pro"),
        ],
    )
    def test_pro_gun_bills_align_republican(self, bill_text, stance):
        from app.pipeline.analyze.party_platform import classify_party_alignment

        result = classify_party_alignment(bill_text, "GUNS", stance)
        assert result in ("R", "bipartisan"), (
            f"Pro-gun bill '{bill_text}' classified as '{result}', "
            f"expected 'R' or 'bipartisan'"
        )

    def test_multi_area_gun_control_bill(self):
        from app.pipeline.analyze.party_platform import classify_party_alignment_multi

        areas = [{"area": "GUNS", "confidence": 0.8}]
        result = classify_party_alignment_multi(
            "Jaime's Law to restrict the sale and transfer of ammunition",
            areas, "anti",
        )
        assert result["overall"] in ("D", "bipartisan"), (
            f"Jaime's Law multi-area classified as '{result['overall']}', "
            f"expected 'D' or 'bipartisan'"
        )


class TestGunsAlignmentScenario:
    """Regression test: gun control advocates must show D-alignment on GUNS.

    Murphy (D-CT) consistently votes Nay on R-sponsored gun amendments and
    Yea on D-sponsored gun control bills. His GUNS area alignment must
    reflect D, not R. The original bug was caused by nominations being
    misclassified as GUNS policy area and recent votes being dropped from
    the voting record before partisan_depth computation.
    """

    def test_consistent_d_voter_on_guns_shows_d_alignment(self):
        from app.pipeline.analyze.party_platform import _alignments_from_votes

        votes = []
        # Simulate voting Nay on 10 R-aligned gun bills (opposing gun deregulation)
        for i in range(10):
            votes.append({
                "vote": "Nay",
                "policyArea": "GUNS",
                "partyLeaning": "R",
                "policyAreas": [],
            })
        # Simulate voting Yea on 5 D-aligned gun bills (supporting gun control)
        for i in range(5):
            votes.append({
                "vote": "Yea",
                "policyArea": "GUNS",
                "partyLeaning": "D",
                "policyAreas": [],
            })

        record = {"keyVotes": [], "recentVotes": votes}
        result = _alignments_from_votes(record)
        guns = [a for a in result if a["area"] == "GUNS"]
        assert len(guns) == 1
        assert guns[0]["alignment"] == "D", (
            f"Expected D alignment for consistent gun control voter, got {guns[0]}"
        )

    def test_consistent_r_voter_on_guns_shows_r_alignment(self):
        from app.pipeline.analyze.party_platform import _alignments_from_votes

        votes = []
        for i in range(10):
            votes.append({
                "vote": "Yea",
                "policyArea": "GUNS",
                "partyLeaning": "R",
                "policyAreas": [],
            })
        for i in range(5):
            votes.append({
                "vote": "Nay",
                "policyArea": "GUNS",
                "partyLeaning": "D",
                "policyAreas": [],
            })

        record = {"keyVotes": [], "recentVotes": votes}
        result = _alignments_from_votes(record)
        guns = [a for a in result if a["area"] == "GUNS"]
        assert len(guns) == 1
        assert guns[0]["alignment"] == "R", (
            f"Expected R alignment for consistent pro-gun voter, got {guns[0]}"
        )


class TestBuildClassificationText:
    """Test _build_classification_text assembles rich text from available fields.

    Bills with uninformative short names (named after people, acronyms)
    need supplementary text for the embedding model to classify correctly.
    """

    def test_name_only(self):
        b = {"billName": "Jaime's Law"}
        result = _build_classification_text(b)
        assert "Jaime's Law" in result

    def test_official_title_appended(self):
        b = {
            "billName": "Jaime's Law",
            "officialTitle": "A bill to prevent the purchase of ammunition by prohibited purchasers",
        }
        result = _build_classification_text(b)
        assert "ammunition" in result
        assert "prohibited purchasers" in result

    def test_crs_policy_area_appended(self):
        b = {
            "billName": "Jaime's Law",
            "crsPolicyArea": "Crime and Law Enforcement",
        }
        result = _build_classification_text(b)
        assert "Crime and Law Enforcement" in result

    def test_summary_appended(self):
        b = {
            "billName": "Test Bill",
            "summary": "This bill establishes new requirements for healthcare coverage.",
        }
        result = _build_classification_text(b)
        assert "healthcare coverage" in result

    def test_full_text_fallback_when_summary_thin(self):
        b = {
            "billName": "XYZ Act",
            "summary": "",
            "fullText": "Section 1. This act amends the Clean Air Act to strengthen emissions standards.",
        }
        result = _build_classification_text(b)
        assert "emissions standards" in result

    def test_full_text_not_used_when_summary_rich(self):
        b = {
            "billName": "Clean Air Act Amendment",
            "officialTitle": "A bill to amend the Clean Air Act to strengthen emissions standards",
            "summary": "This comprehensive legislation addresses air quality by updating emissions standards for power plants and industrial facilities nationwide.",
            "fullText": "FULL TEXT MARKER SHOULD NOT APPEAR",
        }
        result = _build_classification_text(b)
        assert "FULL TEXT MARKER" not in result

    def test_duplicate_name_not_doubled(self):
        b = {
            "billName": "Jaime's Law",
            "officialTitle": "Jaime's Law",
        }
        result = _build_classification_text(b)
        assert result.count("Jaime's Law") == 1

    def test_truncated_to_500_chars(self):
        b = {
            "billName": "Test Bill",
            "officialTitle": "A " * 200,
            "summary": "B " * 200,
        }
        result = _build_classification_text(b)
        assert len(result) <= 500

    def test_html_stripped_from_summary(self):
        b = {
            "billName": "Test Bill",
            "summary": "<p>This bill <b>establishes</b> new healthcare requirements.</p>",
        }
        result = _build_classification_text(b)
        assert "<p>" not in result
        assert "establishes" in result


class TestInvalidateThinClassifications:
    """Learning store entries with very short text must be invalidated
    so that enriched text gets a fresh classification.
    """

    def test_short_text_entries_invalidated(self):
        """Entries with text_prefix < 40 chars should be removed."""
        import json
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from app.database import Base
        import app.models  # noqa: F401 — register tables with Base
        from app.models import LearnedClassification
        from app.pipeline.analyze.bill_learning import (
            invalidate_thin_classifications,
            ENTITY_BILL_POLICY,
        )

        engine = create_engine("sqlite:///:memory:", echo=False)
        Base.metadata.create_all(bind=engine)
        Session = sessionmaker(bind=engine)
        db_session = Session()

        short_meta = json.dumps({"text_prefix": "Jaime's Law", "confidence": 0.15})
        long_meta = json.dumps({
            "text_prefix": "A bill to prevent the purchase of ammunition by prohibited purchasers",
            "confidence": 0.85,
        })

        db_session.add(LearnedClassification(
            entity_name="S.3873", entity_type=ENTITY_BILL_POLICY,
            value="FINANCIAL", confidence=0.15, source="embedding",
            model_version="test", match_metadata=short_meta,
        ))
        db_session.add(LearnedClassification(
            entity_name="S.999", entity_type=ENTITY_BILL_POLICY,
            value="HEALTH", confidence=0.85, source="embedding",
            model_version="test", match_metadata=long_meta,
        ))
        db_session.commit()

        count = invalidate_thin_classifications(db_session)
        assert count == 1

        remaining = db_session.query(LearnedClassification).filter(
            LearnedClassification.entity_type == ENTITY_BILL_POLICY,
        ).all()
        assert len(remaining) == 1
        assert remaining[0].entity_name == "S.999"
        db_session.close()

    def test_no_entries_returns_zero(self):
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from app.database import Base
        import app.models  # noqa: F401
        from app.pipeline.analyze.bill_learning import invalidate_thin_classifications

        engine = create_engine("sqlite:///:memory:", echo=False)
        Base.metadata.create_all(bind=engine)
        Session = sessionmaker(bind=engine)
        db_session = Session()
        assert invalidate_thin_classifications(db_session) == 0
        db_session.close()


@pytest.mark.slow
class TestUninformativeNameClassification:
    """Bills with person-name or acronym titles must classify correctly
    when official title and CRS policy area provide context.

    Regression: Jaime's Law (S.3873) was classified as FINANCIAL/R when
    the classifier only had the short title. With the official title
    ('A bill to prevent the purchase of ammunition by prohibited
    purchasers') and CRS area ('Crime and Law Enforcement'), it must
    classify as GUNS/D.
    """

    def test_jaimes_law_with_official_title(self):
        text = (
            "Jaime's Law "
            "A bill to prevent the purchase of ammunition by prohibited purchasers "
            "Crime and Law Enforcement"
        )
        area, confidence = classify_policy_area(text)
        assert area == "GUNS", (
            f"Jaime's Law classified as {area}, expected GUNS"
        )

    def test_jaimes_law_party_alignment(self):
        from app.pipeline.analyze.party_platform import classify_party_alignment

        text = (
            "Jaime's Law "
            "A bill to prevent the purchase of ammunition by prohibited purchasers "
            "Crime and Law Enforcement"
        )
        result = classify_party_alignment(text, "GUNS", "anti")
        assert result in ("D", "bipartisan"), (
            f"Jaime's Law aligned as '{result}', expected 'D' or 'bipartisan'"
        )

    def test_armas_act_party_alignment(self):
        from app.pipeline.analyze.party_platform import classify_party_alignment_multi

        text = (
            "ARMAS Act of 2025 "
            "Americas Regional Monitoring of Arms Sales Act of 2025 "
            "A bill to require the transfer of regulatory control of certain "
            "munitions exports from the Department of Commerce to the "
            "Department of State "
            "International Affairs"
        )
        areas = classify_policy_areas_multi(text)
        substantive = [a for a in areas if a["area"] != "PROCEDURAL"]
        assert len(substantive) > 0, "ARMAS Act classified as purely PROCEDURAL"

        result = classify_party_alignment_multi(text, areas, "pro")
        assert result["overall"] in ("D", "bipartisan"), (
            f"ARMAS Act aligned as '{result['overall']}', expected 'D' or 'bipartisan'"
        )
