"""Tests for the claim layer — selection logic, no model involved.

Every test names the real published failure it makes unreachable.
"""

from dataclasses import dataclass

from app.pipeline.analyze.claims import (
    Claim, build_facts, build_lede, dedupe_claims, extract_claims, on_topic,
)


@dataclass
class _Article:
    title: str
    summary: str
    source_name: str = "Roll Call"
    url: str = "https://example.test/a"


def _claim(text, name="Roll Call", url="u"):
    return Claim(text=text, source_name=name, source_url=url)


class TestDedupeClaims:
    """A cluster is several outlets on ONE event, so near-identical
    claims are the normal case. Containment, not a similarity threshold
    — nothing here can drift or need recalibrating."""

    def test_keeps_the_more_specific_of_two_nested_claims(self):
        short = _claim("The Senate passed the bill.")
        long = _claim("The Senate passed the bill 60-40 on Thursday.")
        kept = dedupe_claims([short, long])
        assert [c.text for c in kept] == [long.text]

    def test_keeps_genuinely_different_claims(self):
        a = _claim("The Senate passed the funding bill 60-40.")
        b = _claim("Collins opposed the NIH grant order.")
        assert len(dedupe_claims([a, b])) == 2

    def test_drops_an_exact_duplicate(self):
        a = _claim("The Senate passed the bill 60-40.")
        assert len(dedupe_claims([a, _claim(a.text, name="The Hill")])) == 1


class TestExtractClaims:
    SRC = ("A jury found Donald Trump liable for sexual abuse and defamation in the "
           "case brought by E. Jean Carroll.")

    def _article(self):
        return _Article(title="Jury finds Trump liable", summary=self.SRC)

    def test_a_located_assertion_becomes_a_claim_with_provenance(self):
        claims = extract_claims([self._article()], lambda _s: {
            "actor": "Donald Trump", "predicate": "liable for sexual abuse and defamation",
        })
        assert len(claims) == 1
        assert claims[0].text == "Donald Trump liable for sexual abuse and defamation."
        assert claims[0].source_name == "Roll Call"

    def test_the_reversed_party_yields_no_claim(self):
        """Issue #376: both spans are verbatim, so only the
        asserted-together rule in post_composer refuses this."""
        claims = extract_claims([self._article()], lambda _s: {
            "actor": "E. Jean Carroll", "predicate": "liable for sexual abuse and defamation",
        })
        assert claims == []

    def test_an_article_with_no_assertion_yields_nothing(self):
        """The 60-of-160 empty-post shape: no fact, so no text at all."""
        claims = extract_claims([self._article()], lambda _s: {"actor": "", "predicate": ""})
        assert claims == []

    def test_a_failing_extractor_does_not_break_the_cluster(self):
        def boom(_s):
            raise RuntimeError("model down")
        assert extract_claims([self._article()], boom) == []


class TestFactsAndLede:
    def test_facts_and_sources_stay_aligned(self):
        claims = [_claim("A did X.", "AP"), _claim("B did Y.", "Roll Call")]
        facts, sources = build_facts(claims)
        assert facts == ["A did X.", "B did Y."]
        assert sources == ["AP", "Roll Call"]

    def test_facts_keep_the_list_of_strings_shape(self):
        """previous_facts / bsky_posted_facts / newFacts all compare fact
        STRINGS — changing the shape would ripple through all of them."""
        facts, _ = build_facts([_claim("A did X.")])
        assert all(isinstance(f, str) for f in facts)

    def test_the_lede_is_a_verbatim_claim_not_a_synthesis(self):
        claims = [_claim("The Senate passed the bill 60-40."), _claim("B did Y.")]
        assert build_lede(claims) == "The Senate passed the bill 60-40."

    def test_no_claims_means_no_lede(self):
        assert build_lede([]) == ""


class TestTheOldPromptsRulesAreNowStructural:
    """Each of these was a "never / do not" clause in the 4,361-character
    issue prompt — a rule a 1.2B model was asked to remember, which the
    module's own comment admits it does not. None of them is a rule any
    more; each is something the shape cannot express."""

    def _claims(self, source, actor, predicate):
        art = _Article(title=source.split(".")[0], summary=source)
        return extract_claims([art], lambda _s: {"actor": actor, "predicate": predicate})

    def test_a_thing_cannot_be_compared_to_itself(self):
        """Prompt clause: "Comparisons must name TWO DISTINCT entities —
        never write X surpasses X". A self-comparison is not a span of a
        source that compared two different things."""
        src = "Nvidia surpassed Apple in market value on Tuesday."
        assert self._claims(src, "Nvidia", "surpassed Nvidia in market value") == []
        assert len(self._claims(src, "Nvidia", "surpassed Apple in market value on Tuesday")) == 1

    def test_a_concluded_matter_cannot_be_reported_as_ongoing(self):
        """Prompt clause: "If an article says something was dropped,
        dismissed, or ended, the fact must reflect that outcome". The
        same advocacy/state inversion as the Iran headline."""
        src = "Prosecutors dropped the case against the mayor on Friday."
        assert self._claims(src, "Prosecutors", "are continuing the case against the mayor") == []
        assert len(self._claims(src, "Prosecutors", "dropped the case against the mayor on Friday")) == 1

    def test_an_intensifier_cannot_replace_a_number(self):
        """Prompt clause: "Never substitute a vague intensifier
        ('significant', 'sweeping') for a specific number"."""
        src = "The Pentagon cut 1,200 jobs in the reorganisation."
        assert self._claims(src, "The Pentagon", "cut a significant number of jobs") == []
        assert len(self._claims(src, "The Pentagon", "cut 1,200 jobs in the reorganisation")) == 1

    def test_the_coverage_cannot_be_the_subject_of_a_sentence(self):
        """Prompt clause: "Do not write about 'the coverage' or 'the
        reporting' as the subject" — the vacuity that produced 60 of 160
        empty election posts."""
        src = "The coverage emphasises personal connections between the two men."
        assert self._claims(src, "The coverage", "emphasises personal connections") == []


class TestOnTopic:
    """Extraction is faithful to its article, so it is only as good as
    the clustering. Measured live: the cluster titled "Does the UN have
    a future?" contained a BBC housing story and produced "Maricarmen
    was unable to pay the rent set by the firm which recently bought
    it" — true, correctly attributed, and not the issue."""

    def test_a_short_cluster_is_left_alone(self):
        """Nothing to derive a floor from."""
        claims = [_claim("A did X.")]
        assert on_topic(claims, [_Article("t", "s")]) == claims

    def test_an_embedding_failure_keeps_every_claim(self, monkeypatch):
        """Fail OPEN here, unlike the safety checks: this is a ranking
        decision, and losing the model should not silently empty an
        issue."""
        import app.pipeline.analyze.claims as mod

        def boom(*a, **k):
            raise RuntimeError("no model")
        monkeypatch.setattr("app.pipeline.vector_store.get_similarity_model", boom)
        claims = [_claim("A did X.")]
        arts = [_Article("t1", "s1"), _Article("t2", "s2")]
        assert mod.on_topic(claims, arts) == claims


class TestTheLedeIsNotRepeatedAsAFact:
    """Caught by the first end-to-end run against live articles, not by
    any unit test: the Trump/Xi issue's summary and its first key fact
    were the same sentence, because build_lede takes claims[0] and
    build_facts was given the whole list."""

    def test_facts_exclude_the_claim_used_as_the_lede(self):
        claims = [
            _claim("Trump and Xi will hold high-stakes meetings.", "The Hill"),
            _claim("Xi arrived in Washington.", "PBS NewsHour"),
        ]
        lede = build_lede(claims)
        facts, sources = build_facts(claims[1:])
        assert lede == "Trump and Xi will hold high-stakes meetings."
        assert lede not in facts
        assert facts == ["Xi arrived in Washington."]
        assert sources == ["PBS NewsHour"]

    def test_a_single_claim_leaves_no_supporting_facts(self):
        """Which is why the substance gate counts CLAIMS, not facts —
        one claim is a lede with nothing corroborating it."""
        claims = [_claim("Only one thing happened.")]
        assert build_lede(claims)
        assert build_facts(claims[1:]) == ([], [])


class TestExtractionUsesTheWholeCluster:
    """The first production run of the redesign published ZERO issues.

    Its counters showed the pre-existing coherence filter had cut both
    top clusters to 5 articles between them, and at the measured ~39%
    per-article yield that is about one claim per cluster against a
    two-claim substance gate. Two filters, each defensible alone,
    multiplying into nothing.

    Extraction now reads the whole cluster; on_topic() checks each CLAIM
    against the coherent core, which is the right granularity for the
    question and was what the article-level filter was standing in for.
    """

    def test_more_articles_means_more_chances(self):
        arts = [_Article(f"t{i}", f"Person{i} announced a policy change on Tuesday.")
                for i in range(6)]

        def locate(src):
            who = src.split()[1] if len(src.split()) > 1 else ""
            return {"actor": who, "predicate": "announced a policy change on Tuesday"}

        # the whole cluster yields a claim per article...
        assert len(extract_claims(arts, locate)) == 6
        # ...where a coherence-filtered subset of two yields two.
        assert len(extract_claims(arts[:2], locate)) == 2
