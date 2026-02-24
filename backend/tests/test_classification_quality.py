"""
Classification quality evaluation suite.

Provides a hand-labeled holdout set and computes precision/recall/F1
per class for both industry and donor-type classifiers.  Run as a
regression gate when changing thresholds, anchor descriptions, or models.

Usage:
    pytest tests/test_classification_quality.py -v
    pytest tests/test_classification_quality.py -v -k "industry"
    pytest tests/test_classification_quality.py -v -k "donor_type"
"""

import pytest
from collections import Counter, defaultdict

# ── Holdout sets ────────────────────────────────────────────────
# Ground-truth labels verified by hand. DO NOT use these entities
# as training anchors or prototype descriptions.

INDUSTRY_HOLDOUT: list[tuple[str, str]] = [
    # FINANCE — use names with clear financial signals
    ("Goldman Sachs Group", "FINANCE"),
    ("JPMorgan Chase & Co", "FINANCE"),
    ("Charles Schwab Corp", "FINANCE"),
    ("Bank of America Corporation", "FINANCE"),
    ("Morgan Stanley", "FINANCE"),
    # PHARMA
    ("Pfizer Inc", "PHARMA"),
    ("AbbVie Inc", "PHARMA"),
    ("Eli Lilly and Company", "PHARMA"),
    # INSURANCE
    ("Blue Cross Blue Shield", "INSURANCE"),
    ("Aflac Inc", "INSURANCE"),
    ("Progressive Corporation Insurance", "INSURANCE"),
    # HEALTHCARE
    ("Mayo Clinic", "HEALTHCARE"),
    ("HCA Healthcare", "HEALTHCARE"),
    ("American Medical Association", "HEALTHCARE"),
    ("American Dental Association", "HEALTHCARE"),
    # OIL_GAS
    ("Exxon Mobil", "OIL_GAS"),
    ("Chevron Corporation", "OIL_GAS"),
    ("Marathon Petroleum", "OIL_GAS"),
    ("Halliburton Company", "OIL_GAS"),
    # DEFENSE
    ("Lockheed Martin", "DEFENSE"),
    ("Northrop Grumman", "DEFENSE"),
    ("BAE Systems", "DEFENSE"),
    ("L3Harris Technologies", "DEFENSE"),
    # TECH
    ("Microsoft Corporation", "TECH"),
    ("Meta Platforms Inc", "TECH"),
    ("Salesforce Inc", "TECH"),
    ("Oracle Corporation", "TECH"),
    # REAL_ESTATE
    ("National Association of Realtors", "REAL_ESTATE"),
    ("Keller Williams Realty", "REAL_ESTATE"),
    # LAWYERS
    ("Skadden Arps Slate Meagher & Flom", "LAWYERS"),
    ("Covington & Burling LLP", "LAWYERS"),
    ("Latham & Watkins LLP", "LAWYERS"),
    # LABOR_UNIONS
    ("AFL-CIO", "LABOR_UNIONS"),
    ("International Brotherhood of Teamsters", "LABOR_UNIONS"),
    ("United Auto Workers", "LABOR_UNIONS"),
    ("American Federation of Teachers", "LABOR_UNIONS"),
    # ENERGY
    ("Duke Energy", "ENERGY"),
    ("NextEra Energy", "ENERGY"),
    ("Dominion Energy", "ENERGY"),
    # AGRIBUSINESS
    ("Cargill Inc", "AGRIBUSINESS"),
    ("Monsanto Company", "AGRIBUSINESS"),
    # GUNS
    ("National Rifle Association", "GUNS"),
    ("Smith & Wesson Brands", "GUNS"),
    # TRANSPORT
    ("Delta Air Lines", "TRANSPORT"),
    ("FedEx Corporation", "TRANSPORT"),
    ("Union Pacific Railroad", "TRANSPORT"),
    # TELECOM
    ("AT&T Inc", "TELECOM"),
    ("Comcast Corporation", "TELECOM"),
    # TOBACCO
    ("Philip Morris International", "TOBACCO"),
    ("Reynolds American Tobacco", "TOBACCO"),
    # CRYPTO
    ("Coinbase Global", "CRYPTO"),
    # GAMBLING
    ("Las Vegas Sands Corp", "GAMBLING"),
    ("Caesars Entertainment Casino", "GAMBLING"),
    # CONSTRUCTION
    ("Turner Construction Company", "CONSTRUCTION"),
    # EDUCATION
    ("Harvard University", "EDUCATION"),
    # RETAIL
    ("Walmart Inc", "RETAIL"),
    # MANUFACTURING
    ("3M Company", "MANUFACTURING"),
    ("General Motors Manufacturing", "MANUFACTURING"),
    # OTHER — entities that should NOT match any specific industry
    ("Xylophone Kumquat Zephyr LLC", "OTHER"),
]


DONOR_TYPE_HOLDOUT: list[tuple[str, str, str | None]] = [
    # (donor_name, expected_type, candidate_name_or_None)
    # PAC
    ("National Association For Gun Rights INC PAC", "PAC", None),
    ("American Bankers Association PAC", "PAC", None),
    ("Honeywell International PAC", "PAC", None),
    ("Koch Industries Political Action Committee", "PAC", None),
    # Org/Employees
    ("Hampton Inn", "Org/Employees", None),
    ("Andreessen Horowitz", "Org/Employees", None),
    ("Captive Aire Systems", "Org/Employees", None),
    ("Boeing Company", "Org/Employees", None),
    ("Deloitte LLP", "Org/Employees", None),
    ("General Electric", "Org/Employees", None),
    # Party/Ideological
    ("Senate Majority PAC", "Party/Ideological", None),
    ("Club for Growth", "Party/Ideological", None),
    ("EMILY's List", "Party/Ideological", None),
    # CandidateAffiliated
    ("Cruz for Senate", "CandidateAffiliated", "CRUZ, RAFAEL EDWARD"),
    ("Sullivan Victory Fund", "CandidateAffiliated", "SULLIVAN, DAN S"),
    ("Wicker for Senate", "CandidateAffiliated", "WICKER, ROGER F"),
    ("Cassidy for Louisiana", "CandidateAffiliated", "CASSIDY, WILLIAM"),
    ("Friends of Maria Cantwell", "CandidateAffiliated", "CANTWELL, MARIA"),
]


BILL_STANCE_HOLDOUT: list[tuple[str, str, str]] = [
    # (bill_name, policy_area, expected_direction)
    ("A bill to ban assault weapons", "GUNS", "anti"),
    ("A bill to protect voting rights", "JUSTICE", "pro"),
    ("A bill to repeal the Affordable Care Act", "HEALTHCARE", "anti"),
    ("A bill to expand Medicare coverage", "HEALTHCARE", "pro"),
    ("A bill to fund infrastructure programs", "WELFARE", "pro"),
    ("A bill to restrict immigration at the border", "IMMIGRATION", "anti"),
    ("A bill to establish a national cybersecurity center", "TECH", "pro"),
    ("A bill to eliminate the Department of Education", "EDUCATION", "anti"),
    ("A bill to strengthen environmental protections", "ENVIRONMENT", "pro"),
    ("A bill to defund the EPA", "ENVIRONMENT", "anti"),
    ("A bill to increase the minimum wage", "LABOR", "pro"),
    ("A bill to block new drilling permits", "ENERGY", "anti"),
    ("A bill to require universal background checks", "GUNS", "pro"),
    ("A bill to reform the tax code", "TAXES", "neutral"),
]


# ── Metric helpers ──────────────────────────────────────────────


def _compute_metrics(
    predictions: list[str], labels: list[str]
) -> dict:
    """Compute per-class precision, recall, F1, and overall accuracy."""
    classes = sorted(set(labels) | set(predictions))
    tp: Counter = Counter()
    fp: Counter = Counter()
    fn: Counter = Counter()

    for pred, true in zip(predictions, labels):
        if pred == true:
            tp[true] += 1
        else:
            fp[pred] += 1
            fn[true] += 1

    total_correct = sum(tp.values())
    accuracy = total_correct / len(labels) if labels else 0.0

    per_class: dict[str, dict] = {}
    for cls in classes:
        p = tp[cls] / (tp[cls] + fp[cls]) if (tp[cls] + fp[cls]) else 0.0
        r = tp[cls] / (tp[cls] + fn[cls]) if (tp[cls] + fn[cls]) else 0.0
        f1 = 2 * p * r / (p + r) if (p + r) else 0.0
        per_class[cls] = {"precision": round(p, 3), "recall": round(r, 3), "f1": round(f1, 3)}

    return {
        "accuracy": round(accuracy, 3),
        "total": len(labels),
        "correct": total_correct,
        "per_class": per_class,
    }


def _print_metrics(metrics: dict, title: str) -> None:
    """Pretty-print classification metrics."""
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print(f"  Accuracy: {metrics['accuracy']:.1%} ({metrics['correct']}/{metrics['total']})")
    print(f"{'=' * 60}")
    print(f"  {'Class':<22} {'Prec':>6} {'Recall':>6} {'F1':>6}")
    print(f"  {'-' * 46}")
    for cls, m in sorted(metrics["per_class"].items()):
        print(f"  {cls:<22} {m['precision']:>6.3f} {m['recall']:>6.3f} {m['f1']:>6.3f}")
    print()


# ── Tests ───────────────────────────────────────────────────────


class TestIndustryClassificationQuality:
    """Evaluate industry classifier against the holdout set."""

    def test_industry_accuracy_above_threshold(self):
        from app.pipeline.transform.industry_classifier import classify_industry

        predictions = []
        labels = []
        failures = []

        for org_name, expected in INDUSTRY_HOLDOUT:
            result = classify_industry(org_name)
            predictions.append(result)
            labels.append(expected)
            if result != expected:
                failures.append(f"  {org_name}: predicted={result}, expected={expected}")

        metrics = _compute_metrics(predictions, labels)
        _print_metrics(metrics, "Industry Classification Quality")

        if failures:
            print("  Misclassifications:")
            for f in failures:
                print(f)
            print()

        assert metrics["accuracy"] >= 0.80, (
            f"Industry accuracy {metrics['accuracy']:.1%} below 80% threshold. "
            f"Failures:\n" + "\n".join(failures)
        )

    def test_no_critical_misclassifications(self):
        """Entities previously reported as misclassified must be correct."""
        from app.pipeline.transform.industry_classifier import classify_industry

        critical: list[tuple[str, str | set[str]]] = [
            ("Blue Cross Blue Shield", "INSURANCE"),
            ("Hampton Inn", {"RETAIL", "CONSTRUCTION", "OTHER", "REAL_ESTATE"}),
            ("Andreessen Horowitz", {"TECH", "FINANCE", "OTHER"}),
            ("National Rifle Association", "GUNS"),
            ("AFL-CIO", "LABOR_UNIONS"),
        ]
        for name, acceptable in critical:
            result = classify_industry(name)
            if isinstance(acceptable, set):
                assert result in acceptable, f"{name}: got {result}, expected one of {acceptable}"
            else:
                assert result == acceptable, f"{name}: got {result}, expected {acceptable}"


class TestDonorTypeClassificationQuality:
    """Evaluate donor type classifier against the holdout set."""

    def test_donor_type_accuracy_above_threshold(self):
        from app.pipeline.analyze.donor_classifier_ai import classify_donor_type_semantic

        predictions = []
        labels = []
        failures = []

        for donor_name, expected, candidate in DONOR_TYPE_HOLDOUT:
            result = classify_donor_type_semantic(donor_name, candidate_name=candidate)
            predicted = result or "Org/Employees"
            predictions.append(predicted)
            labels.append(expected)
            if predicted != expected:
                failures.append(
                    f"  {donor_name}: predicted={predicted}, expected={expected}"
                    + (f" (candidate={candidate})" if candidate else "")
                )

        metrics = _compute_metrics(predictions, labels)
        _print_metrics(metrics, "Donor Type Classification Quality")

        if failures:
            print("  Misclassifications:")
            for f in failures:
                print(f)
            print()

        assert metrics["accuracy"] >= 0.75, (
            f"Donor type accuracy {metrics['accuracy']:.1%} below 75% threshold. "
            f"Failures:\n" + "\n".join(failures)
        )


class TestBillStanceQuality:
    """Evaluate stance direction derivation against the holdout set."""

    def test_stance_direction_accuracy(self):
        from app.pipeline.analyze.bill_analyzer import derive_stance

        predictions = []
        labels = []
        failures = []

        for bill_name, policy_area, expected_direction in BILL_STANCE_HOLDOUT:
            _, direction = derive_stance(bill_name, "", policy_area)
            predictions.append(direction)
            labels.append(expected_direction)
            if direction != expected_direction:
                failures.append(
                    f"  {bill_name}: predicted={direction}, expected={expected_direction}"
                )

        metrics = _compute_metrics(predictions, labels)
        _print_metrics(metrics, "Bill Stance Direction Quality")

        if failures:
            print("  Misclassifications:")
            for f in failures:
                print(f)
            print()

        assert metrics["accuracy"] >= 0.85, (
            f"Stance accuracy {metrics['accuracy']:.1%} below 85% threshold. "
            f"Failures:\n" + "\n".join(failures)
        )


class TestPolicyAreaClassificationQuality:
    """Evaluate policy area classification for well-known bills."""

    POLICY_HOLDOUT = [
        ("National Defense Authorization Act military spending armed forces", "DEFENSE"),
        ("Affordable Care Act healthcare insurance coverage hospitals", "HEALTHCARE"),
        ("Agriculture Improvement Act farming crop subsidies food stamps", "AGRIBUSINESS"),
        ("Tax Cuts and Jobs Act federal income tax rates deductions", "TAXES"),
        ("Bipartisan Safer Communities Act gun violence prevention background checks", "GUNS"),
        ("SNAP benefits social safety net food assistance housing programs", "WELFARE"),
        ("CHIPS and Science Act semiconductor manufacturing technology", "TECH"),
        ("Clean Air Act emissions pollution EPA environmental standards", "ENVIRONMENT"),
        ("Border security immigration enforcement visa asylum deportation", "IMMIGRATION"),
        ("Student loan forgiveness education act higher education funding", "EDUCATION"),
        ("Wall Street Reform and Consumer Protection Act banking regulation", "FINANCIAL"),
        ("Protecting the Right to Organize Act labor unions collective bargaining", "LABOR"),
        ("Energy Independence and Security Act renewable power grid electricity", "ENERGY"),
        ("United States-Mexico-Canada Agreement trade tariffs imports exports", "TRADE"),
    ]

    def test_policy_area_accuracy(self):
        from app.pipeline.analyze.bill_analyzer import classify_policy_area

        predictions = []
        labels = []
        failures = []

        for bill_text, expected in self.POLICY_HOLDOUT:
            result, _ = classify_policy_area(bill_text)
            predictions.append(result)
            labels.append(expected)
            if result != expected:
                failures.append(f"  {bill_text[:50]}: predicted={result}, expected={expected}")

        metrics = _compute_metrics(predictions, labels)
        _print_metrics(metrics, "Policy Area Classification Quality")

        if failures:
            print("  Misclassifications:")
            for f in failures:
                print(f)
            print()

        assert metrics["accuracy"] >= 0.80, (
            f"Policy area accuracy {metrics['accuracy']:.1%} below 80% threshold. "
            f"Failures:\n" + "\n".join(failures)
        )
