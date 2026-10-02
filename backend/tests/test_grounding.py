"""Tests for deterministic grounding checks on LLM-generated text."""

import pytest

from app.pipeline.analyze import grounding
from app.pipeline.analyze.grounding import (
    editorializing_language,
    grounding_violations,
    hedge_and_editorializing_violations,
    hedge_language,
    intensifier_words,
    log_intensifier_usage,
    ungrounded_electoral_claims,
    ungrounded_numbers,
    ungrounded_titled_names,
)

SOURCE = (
    "Senate votes 68-32 to pass the funding bill. Ruth Pryor said the "
    "$1.2 trillion package includes 120,000 new housing vouchers. The vote "
    "happened on 2026-07-09. Factory fire kills at least 28."
)


class TestUngroundedNumbers:
    @pytest.mark.parametrize(
        "text, expected",
        [
            pytest.param("The 68-32 vote covers 120,000 vouchers", [], id="grounded_numbers_pass"),
            pytest.param("The bill allocates $4.7 billion", ["4.7"], id="fabricated_statistic_flagged"),
            pytest.param("120000 vouchers", [], id="thousands_separator_normalized"),
            pytest.param("a $1.2T package", [], id="currency_and_units_reduce_to_digits"),
            # source has 2026-07-09; "July 9" and "month 7" are the same numbers
            pytest.param("On July 9, 2026", [], id="leading_zero_dates_match"),
            pytest.param("a 0.5 percent cut", ["0.5"], id="decimal_keeps_leading_zero"),
            pytest.param("Senators debated the measure.", [], id="no_numbers_is_clean"),
        ],
    )
    def test_ungrounded_numbers(self, text, expected):
        assert ungrounded_numbers(text, SOURCE) == expected


class TestUngroundedTitledNames:
    @pytest.mark.parametrize("text, source, expected", [
        # "Sen. Pryor" is fine when source says "Ruth Pryor" without title
        pytest.param("Sen. Pryor praised the vote", SOURCE, [], id="titled_name_grounded_by_untitled_source"),
        pytest.param("Senator Whitfield objected", SOURCE, ["Senator Whitfield"], id="invented_official_flagged"),
        # Bare names aren't titled-official claims; other validators own those.
        pytest.param("Whitfield objected", SOURCE, [], id="untitled_names_not_checked"),
        # The name pattern accepts apostrophes so O'Dwyer stays one token,
        # which also swallowed the possessive: "Sen. Pryor's" yielded the
        # surname "pryor's", which no source saying "Pryor" can match, so
        # every possessive reference to a real official read as fabricated.
        pytest.param("Sen. Pryor's amendment advanced", SOURCE, [], id="possessive_of_sourced_official_grounded"),
        pytest.param("Sen. Pryor' amendment advanced", SOURCE, [], id="bare_apostrophe_possessive_grounded"),
        pytest.param("Senator Whitfield's objection", SOURCE, ["Senator Whitfield's"],
                     id="possessive_does_not_ground_an_invented_official"),
        # Apostrophe surnames survive possessive stripping.
        pytest.param("Rep. O'Dwyer filed it", "Rep. O'Dwyer filed the amendment.", [],
                     id="apostrophe_surname_grounded"),
        pytest.param("Rep. O'Dwyer's filing", "Rep. O'Dwyer filed the amendment.", [],
                     id="apostrophe_surname_possessive_grounded"),
        pytest.param("Rep. O'Dwyer filed it", "A bill passed.", ["Rep. O'Dwyer"],
                     id="apostrophe_surname_ungrounded_flagged"),
        # The exact production failure: a role description set off by
        # commas, not a title word directly prefixing the name — the form
        # ungrounded_titled_names didn't cover until this was found.
        pytest.param(
            "The Senate Republican leader, Glen Abbott, has said Whitfield's "
            "death has made a hard month harder for the Senate agenda.",
            SOURCE, ["Glen Abbott"], id="appositive_role_fabrication_flagged",
        ),
        pytest.param(
            "The Senate Majority Leader, Ray Holloway, praised the vote.",
            SOURCE + " Senate Majority Leader Ray Holloway spoke afterward.",
            [], id="appositive_role_grounded_by_source",
        ),
        # Guards against over-matching an ordinary sentence start following
        # some unrelated use of a role word.
        pytest.param("He is the chair. Whitfield spoke next.", SOURCE, [],
                     id="appositive_without_trailing_comma_not_matched"),
        # 2026-07: "affordable" contains "ford"; the old substring check
        # grounded it. Surnames match on word boundaries.
        pytest.param("Rep. Ford praised the measure.", "The affordable housing bill advanced today.",
                     ["Rep. Ford"], id="short_surname_not_grounded_by_substring"),
        pytest.param("Rep. Ford praised the measure.", "Harold Ford spoke in favor of the housing bill.",
                     [], id="whole_word_surname_still_grounds"),
    ])
    def test_ungrounded_titled_names(self, text, source, expected):
        assert ungrounded_titled_names(text, source) == expected


class TestHedgeLanguage:
    @pytest.mark.parametrize(
        "text, expected_count",
        [
            pytest.param("Recent reports say the Senate will vote Thursday.", 1, id="recent_reports_say"),
            pytest.param("Recent reports suggest a delay.", 1, id="recent_reports_suggest"),
            pytest.param("Coverage indicates broad support.", 1, id="coverage_indicates"),
            pytest.param("Sources say the deal is close.", 1, id="sources_say"),
            pytest.param("According to reports, the bill stalled.", 1, id="according_to_reports"),
            pytest.param("The Senate voted 68-32 to pass the bill.", 0, id="direct_reporting_is_clean"),
            # Real Bluesky posts that slipped through the original narrow
            # noun/verb lists (2026-07) — regression tests for the fix.
            pytest.param(
                "Recent discussions emphasize scrutiny of election fraud claims.",
                1, id="recent_discussions_emphasize",
            ),
            pytest.param(
                "Key officials stress transparency and verification remain critical.",
                1, id="officials_stress",
            ),
            pytest.param(
                "Recent reports highlight intensified military actions.",
                1, id="recent_reports_highlight",
            ),
            pytest.param(
                "Recent discussions aim to coordinate meetings for the victims.",
                1, id="discussions_aim_to",
            ),
            pytest.param(
                "Recent discussions focus on election claims and official responses.",
                1, id="discussions_focus_on",
            ),
            pytest.param("RECENT REPORTS SAY the bill passed.", 1, id="case_insensitive"),
        ],
    )
    def test_hedge_language(self, text, expected_count):
        assert len(hedge_language(text)) == expected_count


class TestEditorializingLanguage:
    @pytest.mark.parametrize(
        "text, expected_count",
        [
            pytest.param("The speech is warranted given the senator's concerns.", 1, id="is_warranted"),
            pytest.param("The vote was justified by prior debate.", 1, id="was_justified"),
            pytest.param("This helps advance the legislation.", 1, id="helps_advance_legislation"),
            pytest.param("This helps move the bill forward.", 1, id="helps_move_bill"),
            pytest.param(
                "The Senate passed the bill 68-32 after weeks of debate.", 0,
                id="plain_reporting_is_clean",
            ),
            # Real full-story text that slipped through (2026-07) — the
            # administration's motive was asserted as fact, not reported as
            # a claim or quote from the key facts.
            pytest.param(
                "The administration's actions reflect broader efforts to "
                "manage public perception around election integrity.",
                1, id="reflects_broader_efforts_to",
            ),
            pytest.param(
                "The bill was introduced in an effort to reduce costs.",
                1, id="in_an_effort_to",
            ),
            pytest.param(
                "The campaign aims to shape public perception ahead of the vote.",
                1, id="aims_to_shape_perception",
            ),
        ],
    )
    def test_editorializing_language(self, text, expected_count):
        assert len(editorializing_language(text)) == expected_count


class TestIntensifierWords:
    @pytest.mark.parametrize(
        "text, expected_count",
        [
            pytest.param("The bill raised significant concerns among lawmakers.", 1, id="significant"),
            pytest.param("The package includes sweeping changes to the tax code.", 1, id="sweeping"),
            pytest.param("A major shift in policy followed the vote.", 1, id="major"),
            pytest.param("The Senate passed the bill 68-32 after weeks of debate.", 0, id="plain_reporting_is_clean"),
            pytest.param(
                "Significant and substantial changes followed a dramatic vote.", 3,
                id="counts_distinct_words_once_each",
            ),
        ],
    )
    def test_intensifier_words(self, text, expected_count):
        assert len(intensifier_words(text)) == expected_count


class TestLogIntensifierUsage:
    @pytest.mark.parametrize("text, source, expected", [
        pytest.param("A significant vote occurred.", "The vote was 68-32.", 1,
                     id="increments_when_source_has_a_number_and_text_has_an_intensifier"),
        pytest.param("A significant vote occurred.", "The vote happened today.", None,
                     id="no_increment_when_source_has_no_number"),
        pytest.param("The vote passed 68-32.", "The vote was 68-32.", None,
                     id="no_increment_when_text_has_no_intensifier"),
    ])
    def test_counter(self, text, source, expected):
        from app.pipeline.analyze import action_metrics

        action_metrics.reset()
        log_intensifier_usage("test_surface", text, source)
        assert action_metrics.snapshot().get("intensifier_word_used_test_surface") == expected

    def test_never_raises_on_bad_input(self):
        log_intensifier_usage("test_surface", None, None)  # must not raise


class TestUngroundedElectoralClaims:
    # A source with no electoral vocabulary at all — a senator's death.
    NON_ELECTORAL = (
        "Senator Lowell Whitfield died Thursday at 70. Colleagues including "
        "Ruth Pryor issued statements. Flags were lowered at the Capitol."
    )
    # A source that genuinely covers an election.
    ELECTORAL = (
        "Ruth Pryor faces a competitive re-election campaign. Her "
        "challenger leads in recent polls ahead of the November race."
    )

    @pytest.mark.parametrize(
        "text",
        [
            pytest.param(
                "Whitfield was facing competition from Ruth Pryor for his senate race.",
                id="the_reported_whitfield_pryor_bug",
            ),
            pytest.param("Pryor launched a re-election bid this week.", id="reelection_bid"),
            pytest.param("Whitfield is running against Pryor for the seat.", id="running_against"),
            pytest.param("A primary challenger emerged to unseat the senator.", id="unseat"),
            pytest.param("The senate race between the two tightened.", id="senate_race"),
        ],
    )
    def test_fabricated_electoral_framing_flagged(self, text):
        # Source never mentions an election → electoral framing is invented.
        assert ungrounded_electoral_claims(text, self.NON_ELECTORAL) != []

    @pytest.mark.parametrize(
        "text",
        [
            pytest.param(
                "Pryor faces competition from a challenger in her senate race.",
                id="race_post_grounded_by_race_source",
            ),
            pytest.param("Her re-election campaign drew a new opponent.", id="reelection_grounded"),
        ],
    )
    def test_electoral_framing_grounded_when_source_covers_election(self, text):
        # Source discusses the campaign → the same framing is grounded.
        assert ungrounded_electoral_claims(text, self.ELECTORAL) == []

    @pytest.mark.parametrize(
        "text",
        [
            pytest.param("Opponents of the bill delayed the vote.", id="bill_opponents_not_electoral"),
            pytest.param("The senator issued a statement on the ruling.", id="no_electoral_language"),
            pytest.param("Pryor voted for the funding package.", id="floor_vote_not_electoral"),
        ],
    )
    def test_non_electoral_text_not_flagged(self, text):
        assert ungrounded_electoral_claims(text, self.NON_ELECTORAL) == []


class TestGroundingViolations:
    def test_clean_text_no_violations(self):
        assert grounding_violations("Pryor backed the 68-32 vote.", SOURCE) == []

    def test_reports_both_kinds(self):
        problems = grounding_violations(
            "Rep. Alvarez said the bill adds $9.9 billion.", SOURCE
        )
        assert len(problems) == 2
        assert any("9.9" in p for p in problems)
        assert any("Alvarez" in p for p in problems)

    def test_reports_fabricated_electoral_contest(self):
        problems = grounding_violations(
            "Whitfield was facing competition from Pryor for his senate race.",
            "Senator Whitfield died Thursday. Pryor issued a statement.",
        )
        assert any("electoral contest" in p for p in problems)


class TestHedgeAndEditorializingViolations:
    def test_clean_text_no_violations(self):
        assert hedge_and_editorializing_violations("The Senate voted 68-32.") == []

    def test_reports_both_kinds(self):
        problems = hedge_and_editorializing_violations(
            "Sources say the move was warranted."
        )
        assert len(problems) == 2
        assert any("Sources say" in p for p in problems)
        assert any("was warranted" in p for p in problems)

    def test_hedge_only(self):
        problems = hedge_and_editorializing_violations("Coverage indicates a delay.")
        assert len(problems) == 1
        assert "Coverage indicates" in problems[0]

    def test_editorializing_only(self):
        problems = hedge_and_editorializing_violations("The vote was justified.")
        assert len(problems) == 1
        assert "was justified" in problems[0]

    def test_allow_hedging_suppresses_hedge_and_editorializing_only(self):
        """early_signal.py's developing stories WANT hedging ("coverage has
        not yet appeared" is the honest voice there) — allow_hedging=True
        must suppress hedge_language/editorializing_language specifically,
        while the shape-only checks (never acceptable regardless of
        confidence tier) still fire."""
        problems = hedge_and_editorializing_violations(
            "Sources say the move was warranted.", allow_hedging=True,
        )
        assert problems == []

    def test_allow_hedging_still_flags_placeholders_and_vague_references(self):
        problems = hedge_and_editorializing_violations(
            "Details were shared on [date] by a president.", allow_hedging=True,
        )
        assert any("placeholder" in p for p in problems)
        assert any("single-holder office" in p for p in problems)


class TestGroundingLexicalTightening:
    """2026-07 fixes: non-vacuous electoral context. (The word-boundary
    surname fix is in TestUngroundedTitledNames.)"""

    def test_elected_officials_boilerplate_does_not_disarm(self):
        # "elected officials" and "constituents" are civic boilerplate, not
        # electoral-contest coverage; a fabricated race must still be caught.
        out = ungrounded_electoral_claims(
            "Pryor is facing a primary challenge in her senate race.",
            "Elected officials heard from constituents about the highway bill.",
        )
        assert out

    def test_real_election_coverage_still_disarms(self):
        assert ungrounded_electoral_claims(
            "Pryor is facing a primary challenge in her senate race.",
            "The senate race in Maine tightened as voters weighed the candidates.",
        ) == []


class TestPlaceholderTokens:
    """2026-07 audit: a published fact read "Holloway announced the tribute
    details on [date]." and the Bluesky post shipped with the literal
    "[date]" — every other check in the module is digit- or name-based,
    so nothing fired."""

    @pytest.mark.parametrize("text, expected", [
        pytest.param("Holloway announced the tribute details on [date].", ["[date]"], id="live_date_placeholder_case"),
        pytest.param("The ceremony took place on [specific date] at the cathedral.", ["[specific date]"],
                     id="multiword_placeholder"),
        pytest.param("The Senate voted 68-32 on Thursday.", [], id="clean_text_passes"),
        pytest.param("The tally was [216-212] per the clerk.", [], id="numeric_brackets_pass"),
    ])
    def test_placeholder_tokens(self, text, expected):
        assert grounding.placeholder_tokens(text) == expected

    def test_included_in_hedge_and_editorializing_bundle(self):
        # The bundle is what every publish path (issue text, stories,
        # posts) actually calls — a placeholder must fail it.
        from app.pipeline.analyze.grounding import hedge_and_editorializing_violations
        reasons = hedge_and_editorializing_violations("Details were shared on [date].")
        assert any("placeholder" in r for r in reasons)


class TestUngroundedRelationshipClaims:
    """2026-07 audit: "for the seat left by her brother" published as fact
    with no family vocabulary anywhere in the source — same fabricated-
    relationship class as the electoral-claims guard, family edition."""

    def test_live_brother_case_flagged_without_source_basis(self):
        from app.pipeline.analyze.grounding import ungrounded_relationship_claims
        generated = "She announced her candidacy for the seat left by her brother."
        source = "Whitfield announced a Senate campaign after the incumbent's death."
        assert ungrounded_relationship_claims(generated, source) == ["her brother"]

    def test_grounded_when_source_mentions_family(self):
        from app.pipeline.analyze.grounding import ungrounded_relationship_claims
        generated = "She announced her candidacy for the seat left by her brother."
        source = "Delia Whitfield, sister of the late senator, announced her campaign."
        assert ungrounded_relationship_claims(generated, source) == []

    def test_included_in_grounding_violations_bundle(self):
        from app.pipeline.analyze.grounding import grounding_violations
        reasons = grounding_violations(
            "The seat was held by her brother.", "The seat is vacant."
        )
        assert any("family relationship" in r for r in reasons)


class TestUngroundedFormerOfficialClaims:
    """2026-07 live case: a Bluesky post described "former President Donald
    Varga" while the source material said "President Varga" — the model's
    stale training data demoting a sitting official. No fabricated number,
    grounded surname, no electoral/family claim, and "President" isn't a
    _TITLED_NAME_RE title, so nothing fired."""

    @pytest.mark.parametrize("generated, source, expected", [
        pytest.param(
            "Former President Victor Varga announced new tariffs on Tuesday.",
            "President Varga announced tariffs targeting steel imports.",
            ["Former President"], id="live_former_president_case_flagged",
        ),
        pytest.param(
            "Varga, the former president, signed the order.",
            "President Varga signed the executive order Friday.",
            ["former president"], id="lowercase_and_appositive_forms_flagged",
        ),
        pytest.param(
            "Former President Ferrante criticized the ruling.",
            "Former President Luca Ferrante criticized the court's ruling.",
            [], id="grounded_when_source_says_former",
        ),
        # "former Sen. Smith" in the source grounds "former Senator Smith"
        pytest.param(
            "Former Senator Smith attended the hearing.",
            "The hearing included testimony from former Sen. Jane Smith.",
            [], id="title_abbreviations_ground_each_other",
        ),
        pytest.param(
            "The former vice president spoke at the event.",
            "Nadia Rowe, the former U.S. vice president, spoke Monday.",
            [], id="intervening_words_still_ground",
        ),
        pytest.param(
            "Former Senator Pryor praised the vote.",
            "Ruth Pryor praised the 68-32 vote on the funding bill.",
            ["Former Senator"], id="ungrounded_former_senator_flagged",
        ),
        pytest.param(
            "President Varga announced tariffs. Senator Pryor objected.",
            "Varga tariff order draws objection from Pryor.",
            [], id="current_title_not_flagged",
        ),
        # "former aide" isn't an office this check can verify — out of scope.
        pytest.param(
            "A former aide testified before the committee.",
            "The committee heard testimony Wednesday.",
            [], id="non_office_former_not_flagged",
        ),
    ])
    def test_ungrounded_former_official_claims(self, generated, source, expected):
        assert grounding.ungrounded_former_official_claims(generated, source) == expected

    def test_included_in_grounding_violations_bundle(self):
        from app.pipeline.analyze.grounding import grounding_violations
        reasons = grounding_violations(
            "Former President Varga signed the bill.",
            "President Varga signed the bill Thursday.",
        )
        assert any("former" in r.lower() for r in reasons)


class TestUngroundedPartyClaims:
    """Same stale-training-data class as the 'former' guard above: a model
    attaching a party label from memory rather than the source material."""

    @pytest.mark.parametrize("generated, source, expected", [
        pytest.param(
            "Republican Senator Pryor praised the vote.",
            "Ruth Pryor praised the 68-32 vote on the funding bill.",
            ["Republican Senator"], id="republican_senator_flagged",
        ),
        pytest.param(
            "Sen. Pryor (R-ME) praised the vote.",
            "Ruth Pryor praised the funding bill vote.",
            ["(R-ME)"], id="party_state_abbreviation_flagged",
        ),
        pytest.param(
            "Republican Senator Pryor praised the vote.",
            "Republican Ruth Pryor praised the funding bill vote.",
            [], id="grounded_when_source_states_party",
        ),
        pytest.param(
            "Senator Pryor praised the vote.",
            "Ruth Pryor praised the funding bill vote.",
            [], id="no_party_vocabulary_in_either_not_flagged",
        ),
        # "Senate Republicans agreed" / "Democrats withheld support" is how
        # civic prose overwhelmingly names a party. The singular-only context
        # pattern read those sources as having no party vocabulary at all.
        pytest.param(
            "Republican Senator Pryor praised the vote.",
            "Senate Republicans agreed to drop three riders.",
            [], id="plural_republicans_grounds_the_claim",
        ),
        pytest.param(
            "Sen. Rivera (D-NJ) introduced the bill.",
            "Democrats withheld support for the measure.",
            [], id="plural_democrats_grounds_the_claim",
        ),
        pytest.param(
            "Republican Senator Pryor praised the vote.",
            "Senators agreed to drop three riders.",
            ["Republican Senator"], id="plurals_do_not_ground_a_claim_with_no_party_source",
        ),
    ])
    def test_ungrounded_party_claims(self, generated, source, expected):
        assert grounding.ungrounded_party_claims(generated, source) == expected

    def test_included_in_grounding_violations_bundle(self):
        from app.pipeline.analyze.grounding import grounding_violations
        reasons = grounding_violations(
            "Democratic Senator Rivera introduced the bill.",
            "Alex Rivera introduced a bill Thursday.",
        )
        assert any("party" in r.lower() for r in reasons)


class TestAuditHedgeAndEditorializingAdditions:
    """Regression tests for the 2026-07 audit's phrase additions — each
    parametrized text is a verbatim (or lightly trimmed) published live
    example."""

    @pytest.mark.parametrize(
        "text",
        [
            pytest.param(
                "These developments point to a more nuanced landscape.",
                id="developments_point_to",
            ),
            pytest.param(
                "This development reflects the broader challenges facing congressional leadership.",
                id="development_reflects",
            ),
            pytest.param(
                "The coverage from BBC World and PBS NewsHour captured these developments.",
                id="coverage_captured",
            ),
            pytest.param(
                "These developments underscore the complex interplay between the branches.",
                id="developments_underscore",
            ),
        ],
    )
    def test_new_hedge_verbs_fire(self, text):
        assert hedge_language(text) != []

    @pytest.mark.parametrize(
        "text",
        [
            pytest.param(
                "This shift may influence how political leaders frame their messaging.",
                id="may_influence_how",
            ),
            pytest.param(
                "President Varga's statements shaped the tone of the race.",
                id="shaped_the_tone",
            ),
            pytest.param(
                "The announcement was influenced by public statements, "
                "which influenced the timing of the race.",
                id="influenced_the_timing",
            ),
            pytest.param(
                "The situation remains a point of discussion among officials.",
                id="remains_a_point_of_discussion",
            ),
        ],
    )
    def test_new_editorializing_patterns_fire(self, text):
        assert editorializing_language(text) != []

    def test_plain_factual_reporting_still_clean(self):
        text = (
            "The Senate passed the bill 68-32 on Thursday. Sen. Pryor "
            "voted against it. The measure funds the Pentagon through March."
        )
        assert hedge_language(text) == []
        assert editorializing_language(text) == []


class TestVagueSingularOfficeReferences:
    """2026-07 live case: a Bluesky post read "a U.S. president stated
    fines... should be fully reversed" when the source title was "Varga
    calls for EU investigation into tech fines" — the name was right
    there, but nothing caught the vague indefinite-article phrasing.
    Offices held by exactly one person at a time read as wrong with "a"/
    "an"; "a senator"/"a representative" are excluded since 100/435
    people genuinely hold those."""

    @pytest.mark.parametrize("text, expected", [
        pytest.param(
            "On 2026-07-24, a U.S. president stated fines from the EU "
            "against major tech companies should be fully reversed.",
            ["a U.S. president"], id="live_president_case_flagged",
        ),
        pytest.param("A president signed the order.", ["A president"], id="bare_a_president_flagged"),
        pytest.param("A Speaker announced the vote.", ["A Speaker"], id="speaker_flagged"),
        pytest.param("A chief justice swore him in.", ["A chief justice"], id="chief_justice_flagged"),
        # Legitimately indefinite — 100 senators, 435 representatives.
        pytest.param("A senator introduced the bill.", [], id="senator_not_flagged"),
        pytest.param("A representative from Ohio voted no.", [], id="representative_not_flagged"),
        pytest.param("President Varga signed the order.", [], id="named_president_not_flagged"),
        pytest.param("The president signed the order.", [], id="definite_president_not_flagged"),
    ])
    def test_vague_singular_office_references(self, text, expected):
        assert grounding.vague_singular_office_references(text) == expected

    def test_included_in_hedge_and_editorializing_bundle(self):
        from app.pipeline.analyze.grounding import hedge_and_editorializing_violations
        reasons = hedge_and_editorializing_violations(
            "A U.S. president stated fines should be reversed."
        )
        assert any("single-holder office" in r for r in reasons)


class TestVaguePersonReferences:
    """Live case (issue 522, reported via Bluesky 2026-08-06): a title
    naming Varga was followed by a summary that never named him, instead
    calling him "A political figure from Dale County, Michigan" (that
    location actually describes El-Sayed) and "The individual referenced
    El-Sayed's decision..." — a vague stand-in for a person the piece
    could have just named, the same failure class as
    vague_singular_office_references but for a person rather than an
    office."""

    @pytest.mark.parametrize("text, expected", [
        pytest.param(
            "A political figure from Dale County, Michigan, has voiced "
            "concerns about voting integrity following a recent election "
            "outcome.",
            ["A political figure"], id="live_political_figure_case_flagged",
        ),
        pytest.param("The individual referenced El-Sayed's decision to run again.", ["The individual"],
                     id="the_individual_flagged"),
        # Deliberately excluded, unlike "political figure": "public figure"
        # is the actual defamation-law term (NYT v. Sullivan) for whether a
        # plaintiff must prove actual malice, and this pipeline already
        # covers defamation cases — a real, correctly-sourced fact could
        # legitimately use this exact phrase. Not grounded in an observed
        # live incident the way "political figure" is, so it's left out
        # until one occurs (same discipline as every other pattern here).
        pytest.param("A court ruled she is a public figure for purposes of the suit.", [],
                     id="public_figure_not_flagged"),
        pytest.param("A prominent figure endorsed the candidate.", [], id="prominent_figure_not_flagged"),
        # "the individual" alone is a stock phrase in policy/legal coverage
        # this pipeline already handles — only the reporting-verb
        # construction from the live case ("the individual referenced/
        # said/...") is in scope, not the bare phrase.
        pytest.param("The individual mandate requires most Americans to carry health insurance.", [],
                     id="individual_mandate_not_flagged"),
        pytest.param("The individual was detained pending an immigration hearing.", [],
                     id="individual_detained_not_flagged"),
        pytest.param("The individual was charged with wire fraud.", [], id="individual_charged_not_flagged"),
        pytest.param("Varga commented on the race.", [], id="named_person_not_flagged"),
        pytest.param("El-Sayed ran for the first time in seven years.", [], id="hyphenated_name_not_flagged"),
    ])
    def test_vague_person_references(self, text, expected):
        assert grounding.vague_person_references(text) == expected

    def test_included_in_hedge_and_editorializing_bundle(self):
        from app.pipeline.analyze.grounding import hedge_and_editorializing_violations
        reasons = hedge_and_editorializing_violations(
            "A political figure from Dale County, Michigan, voiced concerns."
        )
        assert any("vague anaphoric reference to a person" in r for r in reasons)


class TestElectioneeringLanguage:
    """2026-09-23 incident. This platform posted "VOTE JANE DOE!
    ... She's better for Jersey than Rivera!" about the NJ Senate race.
    Every existing guard passed it CORRECTLY: the source was a member of
    the public's campaign post, so the endorsement was perfectly grounded,
    and ungrounded_electoral_claims is silent by design whenever the
    source carries real electoral vocabulary. Grounding asks "is this
    faithful to the source"; nothing asked "may we say this at all"."""

    def test_the_post_that_caused_this(self):
        assert grounding.electioneering_language(
            "VOTE JANE DOE! I didn't know a thing about her until I saw "
            "her on the ballot. She's better for Jersey than Rivera!"
        )

    def test_a_perfectly_grounded_endorsement_is_still_refused(self):
        """The whole point: source support is not a defence here."""
        source = "VOTE JANE DOE! She's better for Jersey than Rivera!"
        post = "Vote for Jane Doe."
        assert grounding.ungrounded_electoral_claims(post, source) == []
        assert grounding.grounding_violations(post, source)

    @pytest.mark.parametrize("text", [
        "Vote for Jane Doe in November.",
        "Don't vote for Rivera.",
        "Re-elect Ruth Pryor to the Senate.",
        "Elect Jane Smith this November.",
        "Cast your ballot for Smith.",
        "She is the better choice for New Jersey.",
        "Jones deserves your vote.",
    ])
    def test_flags_voter_directives(self, text):
        assert grounding.electioneering_language(text)

    @pytest.mark.parametrize("text", [
        # The legislative floor-vote sense dominates this domain and must survive.
        "The Senate will vote for the third time on the funding bill.",
        "Pryor said she would vote against the nomination.",
        "The committee voted 12-9 to advance the bill.",
        "The House will vote for passage on Thursday.",
        "Republicans hope to elect a new speaker this week.",
        # Reporting on someone else's endorsement is not endorsing.
        "Smith endorsed Jones in the Senate race.",
        "Rivera leads Doe by six points in a new poll.",
        # A real headline in this database — "Vote" mid-title, not imperative.
        "Twice the House Came One Vote From Telling Varga to End the Iran War",
        "FEC filings list CHRIS COMBS as a candidate in the DE Senate race.",
    ])
    def test_leaves_ordinary_reporting_alone(self, text):
        assert grounding.electioneering_language(text) == []


class TestProposalStatedAsFact:
    """2026-09-23. The Action Center published "Iran War Ends Quickly to
    Lower Prices" above its own accurate summary — officials had CALLED
    FOR an end; the war had not ended. Titles were never run through this
    module, so nothing looked at the part a reader believes first."""

    IRAN = (
        "Recent statements emphasize the need for an immediate conclusion to the "
        "Iran conflict to alleviate rising energy costs. Officials from Michigan "
        "and Iowa have called for measures such as temporary export restrictions "
        "and emergency fuel relief programs."
    )

    @pytest.mark.parametrize("headline, source, expected", [
        pytest.param("Iran War Ends Quickly to Lower Prices", IRAN, ["Ends"], id="the_headline_that_caused_this"),
        # The right sentence about the same source must not flag.
        pytest.param("Lawmakers call for an end to the Iran war to lower prices", IRAN, [],
                     id="reporting_the_call_for_correctly"),
        pytest.param("Officials urge an end to the conflict", IRAN, [], id="reporting_the_urging_correctly"),
        pytest.param("Iran war ends after ceasefire",
                     "The Iran war ended Tuesday when both sides signed a ceasefire.", [],
                     id="an_event_the_source_actually_reports"),
        # Sentence-bounded lookback: a flat character window read the
        # factual second sentence as governed by the first's advocacy.
        pytest.param("Senate passes the bill",
                     "Advocates urged the Senate to pass it. The Senate passed it Thursday.", [],
                     id="a_source_reporting_it_in_a_later_sentence"),
        pytest.param("Senate passes the bill",
                     "Senators should pass the bill, advocates said. The push to pass it continues.", ["passes"],
                     id="still_flags_when_every_mention_is_advocacy"),
        pytest.param("Governor resigns amid probe", "The governor faced questions about the contract.", [],
                     id="a_source_with_no_advocacy_at_all"),
    ])
    def test_proposal_stated_as_fact(self, headline, source, expected):
        assert grounding.proposal_stated_as_fact(headline, source) == expected


class TestEveryPublishingPathIsChecked:
    """Every LLM generation point is classified, and every one that
    publishes prose runs the SHARED combinator.

    The first version of this test asserted the FILE contained
    `grounding_violations(`. action_center.py does — in the issue path —
    so it passed while two other generators in the same file bypassed
    the combinator entirely. Checking per-function instead immediately
    found a third: bluesky_spotlight's _generate_spotlight_post named
    ungrounded_numbers and ungrounded_former_official_claims by hand and
    so never inherited titled-name, electoral, relationship, party or
    electioneering checking.

    That is the failure mode this guards: a module grows its own
    hand-picked subset, and silently misses whatever is added to
    grounding_violations afterwards. A new call_llm site fails this test
    until someone classifies it, which is the point — the classification
    is the review.
    """

    MODULES = [
        "app/pipeline/analyze/action_center.py",
        "app/pipeline/analyze/bluesky_poster.py",
        "app/pipeline/analyze/early_signal.py",
        "app/pipeline/analyze/election_bluesky.py",
        "app/pipeline/justice_pipeline.py",
    ]

    # Functions whose LLM output reaches a reader as prose.
    PUBLISHES_PROSE = {
        "_generate_period_summary",
        "_generate_monitor_metadata", "_run_refresh",
        "_generate_post_text", "_generate_summary",
    }
    # Functions whose LLM output is a located SPAN, verified verbatim by
    # post_composer.compose() rather than by the prose combinator. This
    # is the redesign's shape: the model points at text it did not
    # write, so "is this grounded" is answered by construction and the
    # combinator runs only as a backstop on the composed result.
    SPAN_VERIFIED = {"_locate", "locate_claim"}
    # Functions whose LLM output is a DECISION, never published text.
    # A wrong answer here merges two monitors or mislabels a category —
    # a correctness bug, not a hallucination reaching a reader.
    JUDGMENT_ONLY = {
        "_should_merge_monitors_llm", "_should_match_monitor_llm",
        "_reclassify_monitor_llm",
    }

    def _generators(self, relative_path):
        import ast
        import pathlib as _p

        root = _p.Path(__file__).resolve().parent.parent
        tree = ast.parse((root / relative_path).read_text())
        out = {}
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            called = {
                c.func.id for c in ast.walk(node)
                if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)
            }
            if "call_llm" in called:
                out[node.name] = called
        return out

    @pytest.mark.parametrize("relative_path", MODULES)
    def test_every_generation_point_is_classified(self, relative_path):
        known = self.PUBLISHES_PROSE | self.JUDGMENT_ONLY | self.SPAN_VERIFIED
        unclassified = set(self._generators(relative_path)) - known
        assert not unclassified, (
            f"{relative_path} has unclassified call_llm sites: {sorted(unclassified)}. "
            "Add each to PUBLISHES_PROSE, SPAN_VERIFIED or JUDGMENT_ONLY — "
            "deciding which is the review."
        )

    @pytest.mark.parametrize("relative_path", MODULES)
    def test_prose_generators_run_the_shared_combinator(self, relative_path):
        for name, called in self._generators(relative_path).items():
            if name not in self.PUBLISHES_PROSE:
                continue
            assert "grounding_violations" in called, (
                f"{relative_path}::{name} publishes prose but does not call "
                "grounding_violations. Hand-picking individual checks is how "
                "electioneering_language was missed by three separate modules."
            )
