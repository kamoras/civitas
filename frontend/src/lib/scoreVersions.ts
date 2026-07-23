/**
 * Scoring algorithm version history — the public methodology changelog.
 *
 * Keep in sync with ALGORITHM_VERSION in
 * backend/app/pipeline/analyze/score_calculator.py. Dates are the first
 * pipeline run that produced scores under each version; trend charts use
 * them to mark methodology changes so a score shift from an algorithm
 * update isn't read as a behavior change.
 */

export interface ScoreVersion {
  version: string;
  date: string; // YYYY-MM-DD of first pipeline run under this version
  title: string;
  // One or two plain-language, jargon-free sentences stating what changed
  // and why it matters for a score you might be looking at — the bullet
  // points below stay as detailed and citation-heavy as the methodology
  // warrants, this is the plain-English version for a reader who isn't
  // trying to audit the math. Optional: added 2026-07 to the most-read
  // recent entries rather than retrofitted across the full history.
  tldr?: string;
  changes: string[];
}

export const SCORE_VERSIONS: ScoreVersion[] = [
  {
    version: "v6.11",
    date: "2026-07-23",
    title: "Coalition breadth moves to Legislative Effectiveness; Constituent Alignment gains a real position signal",
    tldr: "Being bipartisan doesn't always mean being aligned with your constituents — a bipartisan member of a lopsided state can be both at once. So the cross-party cosponsorship component moves out of Constituent Alignment and into Legislative Effectiveness, where the research actually supports it (building bipartisan coalitions robustly predicts getting bills through). In its place, Constituent Alignment gains a position-congruence signal: each member's roll-call voting position (DW-NOMINATE, the standard academic measure) compared against what a same-party member of a similarly-leaning seat typically holds — which finally lets a genuinely well-matched member score above neutral, not just at it.",
    changes: [
      "Coalition breadth (cross-party cosponsorship, 20% of Constituent Alignment since v5) is moved out of this dimension. The political-science case for it as a constituent-alignment measure was always weak: voters' demand for bipartisanship varies with the seat's own makeup (Harbridge & Malhotra 2011) — a bipartisan senator from a deeply one-sided state can be admirably bipartisan and still misaligned with what that state elected — and the v6.8 audit had already found the component partially re-measuring this dimension's position signal (r=-0.76). What cross-party cosponsorship robustly predicts is legislative effectiveness: Harbridge-Yong, Volden & Wiseman (2023, Journal of Politics; 93rd-114th Congresses) show members who attract a larger share of their bill cosponsors from the opposing party are substantially more successful lawmakers, for majority and minority members alike. It now lives there as 'Bipartisan coalition attraction' (15%, with bill advancement at 60% and network leadership at 25%; the old 70/30 split returns exactly when cosponsorship data is missing).",
      "Fidelity detail from the same research: the effectiveness effect comes specifically from ATTRACTING cross-party cosponsors to one's own bills — not from offering cosponsorships across the aisle. The new Legislative Effectiveness component therefore uses a receive-only measure computed separately from the blended give-and-receive rate that profile pages continue to display. And unlike the old alignment-side component, it gets no seat-safety discount: in an effectiveness dimension, low bipartisan attraction predicts fewer bills moving regardless of how safe the member's seat is. Two honest caveats are disclosed in the methodology: the evidence is a strong association rather than a clean causal proof, and the measure is an input to effectiveness rather than realized output — both reasons its weight stays modest.",
      "Constituent Alignment gains a position-congruence component (30% when data is available): the member's DW-NOMINATE score — the standard roll-call-based position measure in political science (Voteview; Lewis et al.) — compared against a seat-conditional expectation fit per chamber and per party (what a same-party member of a similarly-leaning seat typically holds; per-party fits deliberately avoid the swing-seat artifact a single pooled fit would create, per Bafumi & Herron 2010). Being 'out of step' with one's district is the misrepresentation construct with the strongest electoral evidence (Canes-Wrone, Brady & Cogan 2002): members positioned toward their party's flank relative to their seat's norm score below neutral (scaled by how unsafe the seat is — flank positions in genuinely safe seats remain the structural norm and are not penalized), and members positioned toward their seat's center score above neutral. That last part closes a long-disclosed gap: a loyalist genuinely matched to their state could previously never score above 50, because no defection-rate measure can see congruence — only a position measure can.",
      "When position congruence is active it replaces the v6.7 position-mismatch discount (which approximated the same construct from cosponsorship patterns — a signal v6.8 found mechanically entangled with bipartisanship); applying both would double-count, the exact failure v6.8 fixed. The old discount remains as the fallback for members the ideal-point data doesn't cover. Every number the new component uses (per-member positions, the seat-conditional fits, the saturation scale) is ingested automatically on every pipeline run from Voteview's published estimates, behind ingestion gates that refuse to store implausible data (a sign flip, a bad join, a truncated file) — none of it is hand-picked, and there is no manual step. If the source is ever unreachable or a gate fails, the previous run's data is kept rather than letting scores degrade; only before the very first successful ingest is the component skipped, with the dimension running on seat-relative vote alignment alone. Ground-truth ranges and the population-spread floor get re-verified on the first run with it active, per the standing convention for any change to this dimension.",
    ],
  },
  {
    version: "v6.10",
    date: "2026-07-23",
    title: "Legislative Effectiveness — closing the last below-neutral tilt",
    tldr: "v6.9 fixed the House/Senate gap but noted a smaller leftover: even within each chamber, slightly more than half of members still landed below the neutral midpoint. That was because members were measured against their chamber's average, and a handful of unusually prolific bill sponsors pull that average above where the typical member sits. This measures everyone against the chamber's midpoint (median) instead, so a typical member now scores near neutral rather than just below it.",
    changes: [
      "v6.9 flagged a residual imbalance it deliberately didn't fix: after the House/Senate correction, both chambers still had slightly more than half of their members scoring below the neutral midpoint on Legislative Effectiveness. This addresses that leftover. It was never a House-vs-Senate problem — it showed up symmetrically in both chambers — but an artifact of the yardstick itself: each member's bill-advancement credit was compared against their chamber's population MEAN, and that distribution is right-skewed. A minority of exceptionally prolific sponsors pull the mean well above where most members sit (live audit: Senate mean 285 vs. median 254; House mean 122 vs. median 107), so comparing everyone to the mean placed most of the chamber below it by construction, regardless of real effectiveness.",
      "Fixed by measuring each member against their chamber's MEDIAN — the actual midpoint member — instead of the mean. A typical member now scores near 50 rather than in the low-40s, and each chamber lands close to an even split around neutral. This is the same 'the median member scores 50' calibration every other Civitas dimension already uses (Funding Independence, Funding Diversity, Constituent Alignment); Legislative Effectiveness was the only one still centered on a mean. The status adjustment for a member's own majority/minority bill mix is unchanged — it still nudges that midpoint bar up or down, it just now nudges around the median instead of the mean.",
      "This is a reference-point recalibration, not a change to how any individual bill is credited: a member's raw record (bills introduced, advanced, and enacted, weighted by significance and cumulative stage) is scored exactly as before — only the population benchmark it's compared against moved from mean to median. The calibration script that derives these constants was updated to report the median, so a future recalibration can't silently revert to the mean.",
    ],
  },
  {
    version: "President v4",
    date: "2026-07-22",
    title: "Historical Legacy's weight held constant — it was never actually 35% for most presidents",
    tldr: "The 35% Historical Legacy weight only applied flatly when every dimension was present. For any president missing mechanical data (nearly everyone before Clinton), the renormalization silently let it rise to 45-62%. Now it's held at exactly 35% whenever there's enough mechanical data to renormalize fairly, with a fallback for the handful of presidents thin enough on data that a single mechanical number would otherwise swamp their real historian rating.",
    changes: [
      "compute_president_overall_score used to renormalize flatly across whichever dimensions had data — so a president missing Agency Alignment (everyone before Clinton, ~36 of 47 presidents) had Historical Legacy's effective weight rise to ~44.7%, and the four non-elected successors missing both Agency Alignment and Public Mandate (Tyler, Fillmore, Arthur, Andrew Johnson) had it rise to ~61.8%. 35% was the true operative weight for only 4 of 47 presidents. Now Historical Legacy is held at exactly its configured weight whenever at least two mechanical dimensions are present, with the mechanical dimensions renormalizing only among themselves for the rest.",
      "Below that two-mechanical-dimension floor, falls back to the old flat renormalization instead of the fixed split — a single mechanical number isn't reliable enough to carry 65% of a score alone. Concretely: Fillmore's only present mechanical dimension, Effectiveness, is 100/100 purely from a Gold-Rush-era GDP boom unrelated to his own governance, while C-SPAN rates him 19/100 — a near-bottom historian reputation. A flat 35%/65% split would have handed him a top-10 placement off that one number; the fallback keeps him where the old (accidentally correct, for the wrong reason) scheme already had him.",
      "Re-verified against the real dataset under the corrected scheme: 35% still keeps Lincoln and Eisenhower in the top 10 and Coolidge/Harding/McKinley out of it — the headline weight didn't need to change, only how consistently it's applied.",
    ],
  },
  {
    version: "President v3",
    date: "2026-07-22",
    title: "Competence removed; Historical Legacy reweighted to 35%",
    tldr: "A closer look at why Calvin Coolidge ranked so high found a real hole: Competence (executive-order signing rate) turned out to have no measurable relationship with real administrative skill, so it's removed entirely — the same standard already applied to Independence and Follow-Through. Historical Legacy's weight is also settled at 35% after two more data-checked revisions, and a real bug (Garfield incorrectly reading as the still-serving president) is fixed.",
    changes: [
      "Historical Legacy's weight went through two revisions after the initial equal-fifths 20%: raised to 50% first (20% let the four mechanical dimensions, which barely track historian judgment at all — Spearman 0.17 — outvote the one dimension that does, putting Coolidge/McKinley/Harding in the top 10 while Lincoln/Eisenhower fell out), then brought back to 35% (at 50%, this platform's ranking correlated 0.958 with simply using C-SPAN's own ranking alone — the mechanical dimensions were contributing almost nothing). 35% keeps the top of the ranking recognizable (FDR, Washington, Lincoln, T. Roosevelt, JFK, Eisenhower) while the mechanical dimensions still meaningfully move the rest (correlation to pure C-SPAN: 0.886, not 0.958).",
      "Competence (executive-order activity rate) is removed entirely. A review of why Coolidge ranked so highly found Coolidge and Harding have nearly identical EO-rates (~216/year each) despite C-SPAN's historians rating their actual administrative skill 596 vs. 334 (of 1000) — almost as far apart as two presidents get. Checked across all 44 rated presidents: EO-rate correlates just 0.097 (p=0.53) with C-SPAN's own 'Administrative Skill' category, statistically no different from noise. Swapping in C-SPAN's Administrative Skill score directly was considered and rejected — it's one of the ten categories C-SPAN itself sums into the same Final Score already driving Historical Legacy, so using both would push this platform's true historian-derived weight toward ~51%, undoing the exact over-reliance-on-C-SPAN problem the 50%→35% revision was built to avoid. Competence's 16.25% is split evenly across the three remaining mechanical dimensions (21.67% each) — Coolidge drops from the top 10 to #12, Harding to #26, McKinley to #17, while Lincoln and Eisenhower both stay in the top 10.",
      "Fixed a real bug: Garfield's UCSB page has no end date at all (he died in office, 1881), which left term_end=None and incorrectly flagged him as the still-serving president. Now backfilled from the next president's (Chester Arthur's) term_start — a generic fix for any future case of the same shape, not a Garfield-specific special case.",
      "Added dimensionsAvailable (0-4) to every president's score: how many of the 4 possible dimensions actually have a value for that specific president, shown on their profile so a score built from partial data isn't read with the same confidence as one built from all 4.",
    ],
  },
  {
    version: "President v2",
    date: "2026-07-21",
    title: "Presidential scoring rebuilt on real data — no more hand-set numbers",
    tldr: "Every presidential score used to rest on a one-time, hand-typed number with no data or citation behind it, presented as if it were computed. It's now computed for real, for every president, from live and historical government/academic/survey datasets — and a dimension with no real source for a given president shows N/A instead of a fabricated placeholder.",
    changes: [
      "Independence and Follow-Through are removed entirely, not just disclosed as limitations — both were always a one-time hand-set number with no live formula and no realistic path to one (Independence's obvious source, OpenSecrets' cabinet/appointee revolving-door API, was discontinued in 2025; Follow-Through needs the same platform-text-vs-action matching already tried four times and abandoned for senators' Promise Persistence, v6.0). Their combined weight first redistributed proportionally across the four remaining dimensions (Public Mandate 15→23%, Effectiveness 20→31%, Competence 15→23%, Agency Alignment 15→23%).",
      "Public Mandate now covers every president who ever won a presidential election, computed rather than curated. Gallup — this platform's original approval source — ended presidential approval tracking entirely in February 2026 after 88 years; approval data now comes from the American Presidency Project (presidency.ucsb.edu), still updated for the sitting president, aggregating AP-NORC/CNN-SSRS/Marist/Pew/Verasight. Presidents before the polling era (pre-Truman) use UCSB's historical election-margin data instead — the average margin of victory across their own election win(s) — both paths z-scored against real population statistics computed from every president's actual data. The five presidents who never won a presidential election in their own right show N/A for this dimension, not a fabricated number.",
      "Effectiveness's GDP-growth component now covers the full presidency: BEA/FRED for the modern era, MeasuringWorth's real-GDP series (1790-present) for earlier presidents, both producing the same term-average-growth figure. Competence's EO-activity-rate component also now covers the full presidency, from UCSB's own executive-order statistics table rather than the Federal Register API, whose machine-readable coverage is a hard wall at 1994.",
      "Every score dimension became nullable rather than defaulting to a neutral 50 or a seeded placeholder — a dimension is null only when it's genuinely inapplicable for that specific president (e.g. Agency Alignment before the Federal Register Act of 1936; Public Mandate for a never-elected president), and the overall score renormalizes across whichever dimensions actually apply, the same 'redistribute onto what's measured' pattern already used within senator/rep scoring for a missing signal.",
      "Identity data (name, party, term dates, term number) is no longer typed into this codebase by hand either — it's fetched live from UCSB's presidential roster on every pipeline run, the same source used for the metrics above. The hand-written narrative summary/achievements/failures fields are removed rather than kept as unscored 'informational' text, for the same reason the two removed score dimensions were removed: no real, citable source behind them.",
      "Review of the first cut of this ranking (before it shipped) found real methodology gaps, not just narrow-scope disclosures: GDP growth was crediting the arithmetic of recovering from a recession, not managed prosperity — Harding's 1921-23 term and FDR's 1933-45 term produced nearly identical average growth (9.36% / 9.19%) purely because both begin at a depression trough, despite very different real stories. Fixed with peak-relative CAGR (from the pre-contraction peak through term-end) whenever a term begins more than 3% below a recent peak — verified against the full dataset, only the 4 presidents with a real, documented financial panic near their term start changed.",
      "Competence's EO-activity-rate used one fixed '30-60/year is optimal' scale for all 235 years. Real data shows a ~10x regime shift exactly at Theodore Roosevelt (1901), not a gradual trend — 'executive order' as a systematic governance tool is largely a 20th-century practice. The old scale scored essentially every pre-TR president as 'very low activity' almost by construction (e.g. Lincoln's 12/year, exactly at his own era's average, read as a weak 37). Now scored by z-score against the president's own era's real population.",
      "Public Mandate's z-score saturation used a hard clamp, making every value beyond 1.5 population standard deviations read identically — Washington, Monroe, Harding, and Coolidge all clamped to the same 100 despite genuinely different landslide magnitudes. Replaced with smooth tanh saturation.",
      "A fifth dimension, Historical Legacy, was added after review found presidents like Lincoln landing in the bottom half of the ranking despite every individual number being defensible — nothing in the first four dimensions could credit 'preserved the Union, ended slavery' at all, because none of them measure historical-consequence leadership. Sourced from C-SPAN's Presidential Historians Survey (2021 cycle, the most recent — 2025 was explicitly postponed by C-SPAN to avoid 'punditry' with a former president back in office) — a real, external, periodically-run survey of ~142 professional historians, categorically different from the hand-set Independence/Follow-Through values removed above. Only rates presidents whose terms were complete as of 2021; every currently-serving or just-departed president shows N/A. Weights reset to equal fifths (20% each) across all five dimensions.",
    ],
  },
  {
    version: "v6.9",
    date: "2026-07-21",
    title: "Legislative Effectiveness — fixing a House/Senate scoring gap",
    changes: [
      "A platform-wide audit (the same night as v6.8, a different dimension) found Legislative Effectiveness compared every member's own majority/minority advancement rate against a single value pooled across both chambers, even though the House and Senate's real advancement rates genuinely differ (House averages meaningfully higher than the Senate). The population-average credit constants this component also uses were already chamber-specific — this one wasn't, and the two were quietly working against each other: it inflated the bar House members were held to and lowered the bar for senators.",
      "Measured effect on live scores before this fix: 61% of House members scored below the neutral midpoint on Legislative Effectiveness, versus only 38% of the Senate — despite no real reason to expect House members to be systematically less legislatively effective than senators once compared fairly against their own chamber's real norms. Splitting this constant by chamber, the same way the related population-average constant already was, brings both chambers to a comparable, much less lopsided split.",
      "A smaller residual imbalance remains in both chambers (slightly more than half of each chamber still lands below neutral) — that traces to comparing members against their chamber's population MEAN rather than its median, which skews the reference point upward since a minority of highly prolific sponsors pull the mean above where most members sit. That's a separate, symmetric effect across both chambers, not the cross-chamber bug this fix addresses.",
    ],
  },
  {
    version: "v6.8",
    date: "2026-07-21",
    title: "Constituent Alignment fairness audit — fixing a double-counted signal",
    tldr: "Two of the checks that can lower a senator's Constituent Alignment score turned out to be measuring the same underlying thing twice, over-penalizing senators like Chris Murphy and Tammy Duckworth who have no real reputation for ideological extremism. This cuts that double penalty back to a single, fair one.",
    changes: [
      "A look at why Chris Murphy (CT) scored so low on Constituent Alignment led to a full population audit (99 scored senators). It found v6.7's position-mismatch discount and this dimension's existing coalition-breadth component aren't independent measurements, even though the formula treated them as two separate signals: within-party ideological extremity and cross-party cosponsorship rate correlate at r=-0.76 (58% shared variance) because both are computed from the same underlying cosponsorship network, just two different mathematical views of it. A member with a narrow, mostly-within-party cosponsorship network was effectively being penalized twice for that one fact inside a single 100%-weighted dimension. This over-penalized senators with no real-world reputation for ideological extremism — Tammy Duckworth (IL), a veteran-focused senator broadly seen as center-left, landed among the '10 most extreme' Democrats by this metric alone, alongside Murphy and Cory Booker (NJ); all three scored 32-36 pre-fix despite representing safe Democratic states.",
      "Two changes, both reusing the seat-safety-scaling mechanism this dimension already relies on elsewhere rather than introducing a new one: the position-mismatch discount's maximum penalty is reduced (roughly to the ~42% of its signal that coalition breadth doesn't already capture), and coalition breadth's below-median case is now itself seat-safety-scaled — a safe seat's narrow, within-party coalition-building may be faithful representation of the coalition that elected the member, the same reasoning v6.6 already applied to below-expected party loyalty, so it's discounted toward neutral there and left at full strength in a swing or opposed seat where it's genuinely legible evidence.",
      "A swing-state member with a real position-mismatch case is still meaningfully discounted — David McCormick (PA) moves to 46, down from a pre-fix 37, a real but no longer punitive penalty; other swing-state Republicans in the same situation stay clearly below neutral too. This narrows, but doesn't eliminate, the overlap between the two signals: they still measure genuinely related aspects of a member's coalition behavior, drawn from the same underlying data source, and a fully independent second signal isn't available without a data source this platform doesn't have.",
    ],
  },
  {
    version: "v6.7",
    date: "2026-07-20",
    title: "Constituent Alignment: a legible discount for out-of-step loyalists",
    tldr: "v6.6 stopped penalizing party loyalty, but left a gap: a senator who never crosses party lines yet holds a position way more extreme than their state elected couldn't be told apart from a genuinely representative loyalist. This adds that check, using each senator's actual ideological position rather than just how often they vote with their party.",
    changes: [
      "v6.6 made below-expected party loyalty floor at neutral (50) because a raw defection rate cannot tell us WHY a member is loyal — it may be faithful representation of their coalition, or it may not be, and the rate alone can't distinguish the two. That left an open question: could a member who votes blatantly out of step with their state — while never crossing party lines — score neutral forever, with no way to tell them apart from a genuinely representative loyalist? Under v6.6 alone, yes. This version adds a second, independent, legible signal the dimension previously ignored: not how often a member crosses, but WHERE they actually stand, measured by ideology_score (derived from cosponsorship patterns, not party labels) against their own party's typical range. A below-expected loyalist whose position sits in their own party's most extreme third, representing a seat that isn't safely aligned for that extremity, is now discounted below neutral — scaled by how unsafe the seat is, so the same extremity in a genuinely safe seat (the structural norm for both parties) is still not penalized. Every other loyalist is unaffected and still scores exactly 50.",
      "Unlike the crossing-side discount still withheld below, this one was fit against real data before shipping: grid-searched against every ground-truth reference range and the population-spread floor using the live scored ideology distribution, and set to reuse this methodology's own existing discount magnitude from the symmetric crossing-side case rather than an arbitrary passing value.",
      "This also tested the stated reason the crossing-side discount below remains unshipped, and found a bigger problem than expected: that reasoning assumed the live ideology data needed to calibrate it doesn't exist outside the production pipeline. It does — this version's own discount is built on it. But checking the crossing-side discount's actual premise against that live data found it wouldn't work even calibrated: the members who cross party lines most often all read as ideologically centrist on their own party's cosponsorship-derived scale, not flank-extreme — the opposite of what that discount assumes. The two signals turn out to be mechanically linked (crossing more and cosponsoring more bipartisan legislation are related behaviors, and the latter is what pulls the ideology score toward the center), not independent measurements of \"how often you cross\" and \"how extreme you are.\" That discount is not just uncalibrated; as designed, it targets the wrong group.",
    ],
  },
  {
    version: "v6.6",
    date: "2026-07-20",
    title: "Constituent Alignment stops penalizing party loyalty",
    tldr: "Senators used to lose points for voting with their party more than expected for their state — even though that's often exactly what their voters wanted. This stops treating high party loyalty as a strike against a senator by itself.",
    changes: [
      "A fairness review asked whether it is fair to score a senator down for \"sticking with their party too much.\" Under v4.2, a member who voted with their party more loyally than their seat's partisan lean \"expected\" was scored below neutral for it — a swing- or opposed-seat loyalist could lose ~14 points purely for a low party-defection rate. Checked against the political-science literature on representation, that isn't supported: a low defection rate is not readable evidence of failing to represent constituents. It may be faithful representation of the coalition that actually elected the member (Fenno 1978; Bishin 2009; Clinton 2006); ~90%+ party-line voting is now the structural norm for both parties even in competitive states (Levendusky 2009; Hopkins 2018), so it carries little individual signal; and being \"out of step\" is a matter of ideological position relative to the district, not a loyalty rate (Canes-Wrone, Brady & Cogan 2002; Krehbiel 2000). Below-expected loyalty now floors at neutral (50) instead of dropping below it. This is the only behavioral change in this version, and it is deterministic — no calibration constant — so it is fully validated: a below-expected loyalist scores exactly 50 on this component. The above-expected crossing side is unchanged.",
      "Designed but deliberately not shipped: a companion fix for the crossing side. Crossing party lines is credited by rate, blind to direction — even though the members who defect most are often ideological extremists breaking from their own flank (Rand Paul from the right, Bernie Sanders from the left), not moderates moving to the center (Kirkland & Slapin 2017). The intended fix discounts crossing credit for members positioned on their party's ideological wing. It is right in direction, but its strength changes real scores for real members and can only be calibrated against the live scored data — so shipping a guessed value would be exactly the kind of unvalidated change that erodes data quality. It is withheld until it can be fit against real data and re-tested, not shipped with a placeholder.",
      "Disclosed limitation of the dimension, not papered over: it measures the rate and direction of a member's deviation from their party, not the distance between the member's position and their constituency's. So it no longer punishes loyalty and it still rewards visible crossing toward the center, but it cannot positively credit representation achieved through congruent loyalty — a member whose party already matches a lopsided state scores a neutral 50, even if perfectly aligned. Measuring that directly (positional congruence) is the honest long-term direction; done right it needs a party-relative target and careful calibration, so it is named as the metric's boundary rather than approximated with half-built code.",
    ],
  },
  {
    version: "v6.5",
    date: "2026-07-19",
    title: "Funding Diversity folded into Funding Independence; Donor Independence removed from Constituent Alignment",
    changes: [
      "v6.0's companion audit found Funding Independence and Funding Diversity correlate at r=0.72 — the same underlying funding-profile signal under two labels, not two genuinely distinct dimensions. v6.0 responded by rebalancing their combined weight down to 33% so the redundant pair couldn't dominate the overall score, but didn't address the redundancy itself. This version folds them into one dimension outright: Funding Independence's weight becomes 33% (the sum of the two prior weights), and Funding Diversity's two signals — source breadth and industry concentration — are now components inside Funding Independence's own score. The internal weights are a straight linear renormalization of each component's prior contribution to the overall score, not a fresh judgment call about relative importance — the underlying continuous math is identical to the pre-merge weighted sum; because scores round to whole numbers, an individual senator's overall score can still shift by a fraction of a point purely from rounding happening once instead of twice, not from any change in what's being measured.",
      "Constituent Alignment's Donor Independence component (25% of that dimension, a heuristic scoring the money associated with donor-vote topical overlaps) is removed. It measured a close cousin of the Funding Independence signal — both driven by total money raised and donor-industry concentration — and in practice reduced to one of four fixed baseline values for 85% of senators, since no data source discloses how a specific donor's money maps to a specific vote (senatorVoteAligned, the field that would carry that signal, is structurally always empty). Its freed weight goes entirely to seat-relative vote alignment; coalition breadth keeps its own independently-justified 20%.",
      "Funding Diversity's underlying computation and score_funding_diversity storage are unchanged and still shown wherever they were before (e.g. Bluesky spotlight text) — this is purely a change to which dimension its signal counts toward in the weighted overall score, the same 'kept running, just excluded from the weighted sum' pattern v6.0 used when removing Promise Persistence.",
      "Legislative Effectiveness's score explanation now breaks a sponsor's substantive-bill count into introduced-only / advanced-further / became-law, instead of a single opaque credit number, after a report that a high score looked wrong for a member who hadn't passed anything. Investigation confirmed the underlying data was correctly scoped to the current term only (not career-cumulative) and the formula itself faithfully implements Volden & Wiseman's real methodology, where introducing a substantive bill earns real credit on its own — the fix was making that visible as real numbers instead of implied by a footnote.",
    ],
  },
  {
    version: "v6.4",
    date: "2026-07-19",
    title: "Academic-fidelity audit: citations corrected, Legislative Effectiveness rebuilt to match its own methodology",
    changes: [
      "A citation review checked every academic paper this scoring methodology cites against what those papers actually say — not just whether the title matched, but whether the specific numbers and formulas attributed to them are really there. Several weren't: a PAC-funding calibration figure attributed to one paper doesn't appear in it (that paper studies something else entirely), and a donor-concentration figure attributed to another paper was similarly unsupported. Both have been corrected — either replaced with a paper that actually contains the relevant finding, or relabeled honestly as this platform's own empirical calibration rather than a borrowed number.",
      "That review also surfaced a real miscalibration, not just a citation problem: re-auditing live data found the typical PAC-funding share is very different for House and Senate candidates (37% vs. 16%), but Funding Independence's PAC-dependency component was using one shared multiplier calibrated against neither figure. It's now chamber-specific, calibrated against each chamber's own real current data.",
      "Funding Diversity's use of an industry-concentration index (HHI) gained a directly-relevant, on-topic academic citation (a 2025 paper that applies the same technique to campaign contributions) in place of a technically-accurate but off-topic one from 1990s banking regulation, plus an honest disclosure of a real, sourced limitation: this kind of index may under-weight the influence of a donor base's most dominant contributors relative to alternative measures.",
      "Legislative Effectiveness is substantially rebuilt. The academic methodology it cited weights each sponsored bill by how significant it is and credits that weight across every stage the bill reaches in the legislative process — a bill that becomes law counts toward every earlier milestone too, not just the final one. The previous version cited this methodology but didn't actually implement it — it used three unrelated measures (a pass/fail rate, a raw bill count, and a network-influence score) instead. It now genuinely follows the cited approach: bills are weighted by significance and credited cumulatively across the stages they reach, compared against what a typical member of that chamber and party status would be expected to achieve. Two honest simplifications: the original methodology has a third significance tier (\"landmark legislation\") based on hand-curated expert judgment this platform has no access to, so only two tiers are implemented; and scores are calibrated against a live empirical average rather than requiring the kind of statistical regression infrastructure the original academic approach used, since this platform's data spans a member's full career rather than one fixed term.",
    ],
  },
  {
    version: "v6.3",
    date: "2026-07-18",
    title: "Funding Independence's small-donor share is now state-population-relative",
    changes: [
      "A population audit (prompted by a look at North Dakota's senators) found the small-donor share component — previously a flat 40%-of-receipts cap for every senator regardless of state — systematically scored small-population states lower: senators from the smallest third of states by population averaged 40 on Funding Independence overall vs. 59.5 for the largest third, a ~19-point gap.",
      "The cause wasn't PAC money: PAC dollar amounts were flat-to-slightly-higher in small states (PACs pay for committee power and access, not local media costs, so the check size doesn't shrink with the state). What actually differs is small-donor fundraising capacity — small states averaged 10.4% small-donor share vs. 23.4% in large states, because larger states have bigger natural donor pools and more national media exposure driving grassroots giving. That's a structural fact about a state's size, not a funding choice by the politician representing it — the same category of problem Independent Voting's v4.2 redesign already fixed for partisan lean.",
      "The small-donor component is now scored relative to what a state's population predicts (fit via regression against live data), the same 'expected vs. actual for this seat' shape as Independent Voting, rather than an absolute cap. PAC dependency (50% of Funding Independence) and top-donor concentration (25%) are unchanged — the data showed no small-state penalty to correct there.",
      "House members are unaffected — congressional districts are apportioned to roughly equal population (~700-800k each) by design, so this state-population bias shouldn't exist at anywhere near the same magnitude there. House keeps the original flat 40%-cap behavior pending a district-level audit.",
    ],
  },
  {
    version: "v6.2",
    date: "2026-07-16",
    title: "Legislative Leadership no longer treats every cosponsorship equally",
    changes: [
      "External review, 2026-07: cosponsorship-network centrality alone can't distinguish a substantive bill from a message bill introduced purely for the cosponsor list — a senator who signs onto ten resolutions with zero chance of passing accrued the same PageRank weight as one who cosponsors ten bills that actually became law.",
      "Each cosponsorship is now weighted by what happened to the underlying bill: full weight if it became law, 60% weight if it passed a chamber or was ordered reported out of committee, 30% weight if it never advanced. A stalled bill still counts — it's real evidence of a working relationship, just weaker evidence of productive collaboration than one that cleared a real procedural hurdle. Calibrated against the live cosponsorship corpus, where the large majority of any two-year sample of sponsored bills (roughly 90-95%) never advances at all, so a zero weight for stalled bills would have collapsed the network into near-total sparsity.",
      "This affects only Legislative Leadership (and, through it, 30% of Legislative Effectiveness). Ideology deliberately keeps the original flat weighting — a symbolic resolution that never advances is often exactly where partisan alignment shows up most clearly, so discounting it there would remove signal rather than noise.",
    ],
  },
  {
    version: "v6.1",
    date: "2026-07-16",
    title: "Funding Diversity's small-donor ceiling raised for overwhelmingly grassroots-funded members",
    changes: [
      "A population-wide check found no senator scored above 69 on Funding Diversity — the single most grassroots-funded senator by small-donor share scored exactly 69, the same as anyone just barely over an internal 30% threshold. The industry-concentration half of this score falls back to a flat neutral value when a member has too little classified-industry money to measure meaningfully; that fallback used to be a flat number regardless of whether a member was just over the threshold or almost entirely small-dollar funded.",
      "The fallback now scales with how dominant small-donor funding actually is, instead of a one-size-fits-all value — a member relying almost entirely on small donors is treated as close to maximally diversified (since that money is spread across an effectively unbounded number of individual contributors), rather than capped at the same score as someone only modestly grassroots-funded.",
      "This only raises scores for members whose funding leans heavily small-dollar; it does not change how concentrated industry-sourced money is scored.",
    ],
  },
  {
    version: "v6.0",
    date: "2026-07-15",
    title: "Promise Persistence removed as a scored dimension",
    changes: [
      "This is the fourth attempt at fixing Promise Persistence (v5, v5.1/v5.3, v5.4, v5.10) without resolving the underlying gap. A live measurement across all 100 senators found 0 reached even \"medium\" confidence per the platform's own thresholds (3+ evaluable promises) — mean 0.3 evaluable promises per senator, 76% with zero. Real campaign promises are generic platform language (\"Expand Medicare coverage\"), and semantic matching against specific vote/bill text structurally can't bridge that gap: genuinely-related votes for real promises typically score below the match threshold, not above it.",
      "Promise Persistence's 25% weight is redistributed proportionally across the remaining four dimensions: Funding Independence 15%→20%, Constituent Alignment 25%→33%, Funding Diversity 10%→13%, Legislative Effectiveness 25%→34%.",
      "Campaign-promise extraction and kept/broken/partial tracking are unchanged and still shown on every member's profile — this is purely a scoring-weight change, not a data-collection change. The underlying promise data remains real and worth reading; it's just no longer folded into the weighted 0-100 composite score.",
      "A companion composite-validity audit found Funding Independence and Funding Diversity correlate at r=0.72 across the live Senate population — both driven by the same underlying funding-profile signal (grassroots small-dollar money scores well on both; PAC/large-donor-heavy fundraising scores poorly on both), not the independent dimensions each is meant to measure. At the prior 25%/15% weights that correlated pair carried 40% combined, nearly double any other single dimension, and could single-handedly override strong performance elsewhere — the audit's reference case: the sitting Senate Majority Leader ranked 2nd-from-last Senate-wide almost entirely because of this pair, despite above-median scores on the other three dimensions. The redistribution above accounts for this: the correlated pair's combined weight lands at 33%, matching what one genuinely distinct dimension gets, with the remainder split toward the dimensions confirmed empirically uncorrelated with each other and with this pair (pairwise |r| < 0.31).",
    ],
  },
  {
    version: "v5.12",
    date: "2026-07-13",
    title: "Legislative Effectiveness no longer penalizes freshman senators for lack of time",
    changes: [
      "A leaderboard review found newer senators scoring dramatically lower on Legislative Effectiveness for reasons that traced back to time in office rather than actual behavior — freshman senators (2 years or less) averaged 29.5 on this dimension versus 54.1 for senators with 10+ years, a gap far larger than any other dimension shows.",
      "The main driver: the coalition-building sub-component measures how central a member is in the chamber's cosponsorship network, which takes years to build regardless of how effective a new member is — and missing data was previously defaulting to a below-neutral penalty instead of a neutral score. Both are fixed: missing data now defaults to neutral like every other component, and the raw network-centrality measure is now weighted toward neutral for members still in their first term, reaching full weight by the 6-year mark.",
      "This narrows the freshman/veteran gap by about a quarter — a real improvement, not a complete fix. Some of the remaining gap likely comes from other sub-components (bill advancement genuinely takes time regardless of effectiveness) that weren't touched in this pass.",
    ],
  },
  {
    version: "v5.11",
    date: "2026-07-13",
    title: "Lobbying-connection detection: substantial funding, not any funding",
    changes: [
      "User report: essentially any vote near any donor was being flagged as a \"lobbying connection,\" regardless of how small the donor was or how loosely related the vote actually was — a senator voting for the annual budget with an unrelated small donor shouldn't register as a finding.",
      "Donor-vote connections now require two things together: the donor's industry must represent a substantial share (25%+) of the member's classifiable industry funding — not total funding raised, which is mostly small-dollar and non-industry money by nature and made \"substantial\" meaningless as a bar — and the vote must be in that industry's actual policy domain, checked against a properly calibrated similarity measure instead of raw, noisy text comparison.",
      "This feeds the donor-influence component of Constituent Alignment, so scores may shift for members whose previous matches were mostly false positives under the old, much looser criteria.",
    ],
  },
  {
    version: "v5.10",
    date: "2026-07-13",
    title: "Promise Persistence data-scarcity fixes",
    changes: [
      "Fixed a bug where a member's own ceremonial resolutions (e.g. a sorority-anniversary resolution, an 'awareness day' designation) could be converted into a tracked \"promise\" when platform text was sparse — then inevitably scored unclear, since a resolution agreed to without floor debate has no matching vote to check against. These no longer count as promises at all.",
      "Promise Persistence's confidence-shrinkage math is recalibrated for how little verifiable promise evidence actually exists today (average under 1 evaluable promise per senator — most campaign promises are broad and aspirational, not tied to a specific bill that comes up for a floor vote within one congress). Without this, nearly every senator was scoring in a narrow, nearly-identical band regardless of their real record. This is a stopgap for today's evidence volume, not a claim that the underlying evidence-matching is now complete — that's flagged as ongoing work.",
    ],
  },
  {
    version: "v5.9",
    date: "2026-07-13",
    title: "Legislative Effectiveness: resolutions no longer count as volume",
    changes: [
      "Fixed a live bug: a Senator's \"National Mushroom Day\" resolution — a ceremonial measure agreed to without debate — was inflating their Legislative Effectiveness score. The advancement component already excluded commemorative resolutions (since v4); the volume component didn't, so the two had silently drifted out of sync. Volume now counts substantive legislation only.",
      "Volume ceilings recalibrated to the substantive-only distribution now that resolutions are excluded (they had inflated the raw per-congress top-decile by roughly 14-16%).",
      "Fixed a related inconsistency this exposed: a member with zero substantive bills but some resolutions was scoring worse on volume than a member who sponsored nothing at all. Both now score the same honest neutral baseline.",
    ],
  },
  {
    version: "v5.8",
    date: "2026-07-12",
    title: "Scores reflect the current term, not a member's whole career",
    changes: [
      "Votes, sponsored bills, and Legislative Effectiveness now window to the current congress only, down from a rolling 2-3 congress lookback. A member who did strong work a decade ago and has coasted since no longer gets credit for it on every run — scores now measure what a member is doing right now, and reset at the start of each new congress.",
      "Funding Independence and Funding Diversity window to the member's most recent election only, down from the two most recent. This uses a different rule than votes/bills on purpose: Senators legitimately raise little money in the non-election years of a 6-year term, so tying funding to a strict 2-year window would go near-empty most of the time for reasons that have nothing to do with coasting. Tying it to their current campaign instead fixes the same staleness problem without that gap.",
      "Confidence badges (the data-sufficiency marker shown next to sparse scores) are recalibrated for the narrower vote/bill windows, so a member isn't marked low-confidence purely because the window got stricter by design.",
      "The score trend chart now marks congress boundaries (in addition to methodology-version changes) so a shift at the start of a new congress reads as an intentional reset, not a bug.",
    ],
  },
  {
    version: "v5.7",
    date: "2026-07-12",
    title: "Removed noise-driven secondary policy-area tags",
    changes: [
      "Bills, votes, and Action Center issues could previously be tagged with up to 3-4 policy areas each, on the theory that real legislation and news often span multiple domains. A follow-up to the classification audit measured the actual gap, across 60 real Action Center issues, between the top-scoring category and the runner-up: a median of 0.018 (90th percentile: 0.053) out of a 0-1 similarity scale. Every category clusters within a few hundredths of each other for almost any input text, so the \"secondary area\" filter was effectively noise — a story about a murder trial was tagged Technology; a story about an accounting firm's collapse was tagged Technology and Energy without ever confidently noting it was about financial matters at all. Classification is single-area only until a genuinely discriminating signal for secondary relevance exists.",
    ],
  },
  {
    version: "v5.6",
    date: "2026-07-12",
    title: "Fairness audit — House scoring bugs, bill/vote classification imbalance",
    changes: [
      "A fairness pass comparing scores across party, seat safety, seniority, and chamber found the House-vs-Senate gap in Legislative Effectiveness was mostly a bug, not real: the sponsorship-volume ceiling had been calibrated only against Senate data (435 House members structurally introduce far fewer bills per congress than 100 senators, splitting similar institutional bandwidth), and a separate pipeline bug was silently discarding any House member's sponsored-bill data past the 50th bill — 39% of the House hit that cap exactly, undercounting both the volume and advancement components. Both are fixed; House Legislative Effectiveness's population average moved from well below the neutral midpoint to in line with the Senate's.",
      "Verified as real, not bugs: Democrats' higher average Funding Independence traces to a genuine, measurable difference in how the two parties' campaigns are financed (PAC-reliance roughly double for Republicans in current data), and House members' lower Funding Independence traces to House races being structurally cheaper and more PAC-dependent than Senate races. Both are long-documented patterns in campaign-finance research, not something the formula treats parties or chambers differently to produce.",
      "Fixed a structural weakness in the adaptive bill/vote classifier: its self-training reference corpus is heavily imbalanced (some policy categories had zero examples), and a confident vote from that corpus was allowed to override an otherwise-correct match against the hand-written category descriptions — even when the corpus literally couldn't have produced the right answer because it had never seen an example of that category. A confident classifier vote is now only trusted over the category-description match when the corpus has meaningful representation of the alternative; otherwise the category-description match wins. Measured accuracy on a held-out policy-area test set: 78.6% before, 100% after.",
    ],
  },
  {
    version: "v5.5",
    date: "2026-07-12",
    title: "Funding Diversity data bug, Legislative Effectiveness recalibration",
    changes: [
      "Fixed a data bug in Funding Diversity's industry-concentration signal: UNCLASSIFIED (donations the classifier couldn't attribute to any industry) was being treated as a legitimate industry rather than excluded like OTHER/POLITICAL, and the concentration math used each industry's rounded display percentage instead of its actual dollar total — an industry under roughly 0.5% of a senator's total raised rounds to 0% and vanished from the calculation entirely. Together these made 95 of 100 senators look almost totally concentrated in a single 'industry' (usually UNCLASSIFIED) regardless of their real donor spread, dragging the population average well below every other dimension's neutral calibration point. Concentration is now computed from real dollar totals.",
      "Legislative Effectiveness's sponsorship-volume ceiling (bills introduced per congress served) is recalibrated: it was set in 2026-06 against a full-credit ceiling just above that period's 90th percentile, but the live distribution has since grown enough that over a fifth of senators were fully saturating the old ceiling, scoring identically regardless of how far past it they were. Reset to restore the same top-decile headroom the ceiling originally provided.",
      "Documented (not changed): the donor-independence component of Constituent Alignment always runs on its reduced-weight fallback, because no lobbying-disclosure source this platform ingests discloses which way a donor's industry wanted a given bill to go — only aggregate spend. Filling that in would require hand-authoring an industry-to-position assumption, which this platform's scores are built never to contain.",
    ],
  },
  {
    version: "v5.4",
    date: "2026-07-12",
    title: "Promise-evidence gray-zone gate",
    changes: [
      "v5.3's Promise Persistence shrinkage-prior resize didn't fix the underlying collapse: a self-check after the 2026-07-11 run measured population stdev at 3.72, still below the 8.0 floor that check exists to enforce. The real cause was upstream — the 0.80/0.82 relevance threshold was calibrated against promises that quote a bill by name (true matches score 0.77-1.0), but most real campaign promises are generic platform language whose best genuinely-related vote typically scores only 0.65-0.75, never clearing the bar regardless of the shrinkage prior's size. Only 31 of 100 senators had any evaluable promise at all.",
      "Below the existing high threshold, a gray zone now goes to an LLM that reads the actual promise and candidate vote/bill text and judges genuine relatedness, instead of being dropped outright. At or above the threshold nothing changes — no new LLM calls, no regression risk for bill-quoting promises — and the check fails closed on any LLM error. The House pipeline is unaffected and keeps its original deterministic cutoff.",
    ],
  },
  {
    version: "v5.3",
    date: "2026-07-11",
    title: "Promise Persistence recalibration and confidence-badge fix",
    changes: [
      "Promise Persistence's shrinkage prior is recalibrated for v5.1's stricter evidence thresholds. That recalibration (0.80/0.82 relevance) fixed a real false-positive problem but also roughly halved how many promises are evaluable per member — with the shrinkage prior left at its old size, it came to dominate almost every score, collapsing Promise Persistence toward a narrow neutral band regardless of a member's actual record (stdev fell from ~7 to ~3 in testing). The prior is resized to restore the original balance between evidence and shrinkage for the new, smaller-but-more-accurate evidence pool.",
      "Fixed a bug where every senator's data-sufficiency confidence badges (the 'low data' marker shown next to sparse scores) were silently dropped before saving, since 2026-07-04 — representatives were unaffected. Confidence badges now display correctly for senators too.",
      "Added an automatic check after each pipeline run that flags if any score dimension's spread collapses across the full senator population, so a recalibration mistake like the Promise Persistence one above gets caught immediately instead of by manual audit.",
      "Action Center's full-length issue articles now scale their target length to how much reporting actually exists for that issue, instead of a fixed word count — thin coverage was previously padded out with invented specifics (fabricated dates, statistics, and international-agreement details not present in any source) to hit the old floor. Generated articles are also checked for and rejected if they repeat the same sentence verbatim, and mechanical fact-checking now catches fabricated years and physical-magnitude figures (e.g. degrees) in addition to money and percentages.",
      "Bluesky senator spotlight posts no longer describe a middling score as a 'standout' — the post now highlights a specific dimension only when it's genuinely far from the population's neutral point; when none of a senator's five scores are unusual, the post states the overall ranking plainly instead of praising or criticizing an unremarkable number.",
    ],
  },
  {
    version: "v5.2",
    date: "2026-07-11",
    title: "District-level seat expectations and donor-record integrity",
    changes: [
      "House Constituent Alignment now measures each representative against their DISTRICT's partisan lean (Cook PVI, ingested per-district) instead of their state's. State lean was structurally unfair in split states: a member elected by a D+19 urban district in a red state was scored as holding an 'opposed seat' and expected to vote against their party ~20% of the time — when their district elected exactly the platform they vote for. Senators are unchanged (their constituency is the state).",
      "Donor records now include only actual contributions (FEC Schedule A line 11). The receipt itemization also contains joint-fundraising transfers, campaign loans, vendor refunds, and bank interest — all of which were being listed as 'donors': one senator's #1 donor was a media-buying vendor's refunds, and banks that merely lent to campaigns appeared as top donors. This corrects top-donor concentration (part of Funding Independence), Funding Diversity, profile donor lists, and donor-vote matching.",
      "The candidate's own contributions (line 11D) are identified by FEC line number rather than name matching, making self-funding exclusion deterministic.",
      "All public LLM-generated content (issue facts, full stories, Bluesky posts) now passes mechanical grounding checks: numbers and titled-official references must appear in the source material the text was generated from, or the content is rejected. Spotlight posts previously also computed their headline 'overall score' as an unweighted average — they now show the same weighted composite as the leaderboard.",
    ],
  },
  {
    version: "v5.1",
    date: "2026-07-10",
    title: "Funding-window and promise-evidence data corrections",
    changes: [
      "Fixed a funding-window bug that counted the same election twice for roughly a third of members: the FEC totals endpoint returns both an election-full aggregate row and partial per-cycle rows for the same race, and the 'two most recent elections' window could pick two rows from the same election — double-counting a partial window and dropping the member's previous (often much larger) race entirely. Funding totals now take one row per election. Funding Independence changes materially for affected members in both directions (recently-elected members whose real race had fallen out of the window recover; members whose small partial window was double-counted lose inflated scores).",
      "Promise-vote relevance thresholds recalibrated against the embedding model's measured noise floor. Any two pieces of formal legislative text score ~0.55-0.87 cosine similarity from shared register alone; the old thresholds (0.28/0.40) sat entirely inside that noise, so the alignment engine cited whatever ranked highest among unrelated votes as 'evidence' for a promise. New thresholds (0.80/0.82) plus a promise-category / vote-policy-area compatibility gate cut cross-domain false positives to ~1-2% while keeping ~90% of true matches. Expect fewer but far more trustworthy kept/broken verdicts, with more promises honestly marked unclear.",
      "Promises that name a specific bill are now scored by whether the member voted the way they said they would, instead of by the bill's generic pro/anti policy direction — a Yea on 'Ending X Act' no longer counts as breaking a promise to support ending X.",
      "Floor-speech advocacy classification fixed: at the old similarity threshold every sampled speech matched all 14 policy categories (a school-recognition speech 'advocated' on guns, taxes, and immigration), feeding a constant near-perfect advocacy signal into Promise Persistence for anyone with any floor remarks. Remarks now match at most one category at a calibrated threshold; ~29% of real remarks classify, matching the observed ceremonial/substantive split.",
    ],
  },
  {
    version: "v5",
    date: "2026-07-05",
    title: "Representing all constituents — coalition breadth and status-fair effectiveness",
    changes: [
      "Constituent Alignment gains a coalition-breadth component (20%): the rate at which a member attracts cosponsors from the other party and cosponsors the other party's bills, normalized to the chamber median (Lugar Center Bipartisan Index method). Voting congruence asks whether you vote the way your seat elected you to; breadth asks whether you also legislate for the constituents who didn't vote for you. Cohort-median crossing scores 50; the normalization is recomputed from observed behavior every run, so it is party- and majority-symmetric with no fixed constants.",
      "Legislative Effectiveness advancement is now benchmarked against the sponsor's majority/minority status in each congress. Measured from our own bill corpus: senate majority sponsors advance 3.6% of substantive bills vs 2.4% for the minority; house 6.4% vs 2.4% (consistent with Volden & Wiseman 2014). The old absolute 5% threshold silently penalized whichever party was out of power; matching your status baseline now scores 50 for everyone.",
      "Senators with sparse platform data get their Promise Persistence positions augmented from their own sponsored legislation (the deterministic House path from v4.3), deduplicated against stated platform promises. This closes most of the v4.3 cross-chamber shrinkage offset by giving both chambers comparable evaluable-position counts.",
      "Action center ranking now leads with civic actionability (40%) over coverage breadth (35%) and trending (25%): a story where citizens can directly act outranks a better-covered story with no US action surface.",
    ],
  },
  {
    version: "v4.3",
    date: "2026-07-05",
    title: "Real House promise data",
    changes: [
      "House members now have evaluable promise data. Representatives' positions are derived from their sponsored legislation (bills they introduce) rather than campaign platform text, which is scarce for House members. Each representative's positions are extracted from bill topics and evaluated deterministically against their floor votes using the same embedding rules as the Senate path.",
      "Position source integrity: sponsored bills are excluded from a position's evidence entirely — positions are evaluated against floor votes only — so a representative can never 'keep a promise' simply by introducing the bill that defines it. Sponsoring is effort, not outcome.",
      "Near-duplicate topic deduplication uses a similarity threshold (0.88) derived empirically from the distribution of same-member bill-title similarities, rather than hand-picked. This prevents a representative with many related bills on the same topic from appearing to have independent evidence across redundant positions.",
      "Published confidence markers (high/medium/low) now reflect real evaluable-promise counts for House representatives. Previously all 431 representatives had zero promises, collapsing their Promise Persistence scores to neutral. Now the dimension is data-driven for both chambers.",
      "Known cross-chamber offset, disclosed rather than tuned away: House Promise Persistence runs a few points above the Senate's (shadow-validated means 64 vs 58) because representatives average ~8 evaluable positions to senators' ~2, so House scores are shrunk less toward the neutral prior. The underlying kept-rates are similar (79% vs 74%). Forcing the means equal would require a hand-fed chamber constant, which the methodology forbids.",
    ],
  },
  {
    version: "v4.2",
    date: "2026-07-05",
    title: "Constituent Alignment — representation, not defection",
    changes: [
      "Mission clarified: scores measure how well members represent their constituents — not independence as an intrinsic virtue. Party-line voting in a seat that elected that platform is representation.",
      "Independent Voting renamed Constituent Alignment and rebuilt: each member's contested-vote break rate is now scored against a seat-specific expectation derived from state partisan lean (Cook PVI). Matching the seat's expectation scores ~50 (\"typical partisan for this seat\"); hyper-loyalty in a swing or opposed seat drifts below neutral instead of to a failure grade.",
      "Crossing party lines is not rewarded for its own sake: surplus crossing earns credit only where it plausibly moves toward the state's median voter (opposed and swing seats). In deep aligned seats it sits near neutral — break direction relative to state opinion is unobservable, so safe-seat defection is treated as neither virtue nor defiance.",
      "Removed the hard floor that pinned any break rate under 3% at a score of ~20 — it placed 73 of 100 senators in an indistinguishable 26-38 band and labeled typical representation a failure.",
      "Removed the exemption for party-line votes on policy areas related to a member's top donor industries. Donor industries are not a proxy for state interests; that exemption shielded exactly the votes most suspect for donor influence.",
    ],
  },
  {
    version: "v4.1",
    date: "2026-07-03",
    title: "Adversarial review fixes",
    changes: [
      "Candidates' own money (self-loans) is deterministically excluded from top-donor concentration and donor-vote matching — it was previously mistyped as employer money and 19 senators appeared as their own biggest donor.",
      "PAC dependency now also weighs absolute PAC dollars, so very large campaigns can't dilute millions in PAC money to a near-zero share.",
      "Donor-vote overlap descriptions were reworded: totals are employer-aggregated contributions, not single org-level donations, and overlap does not imply influence. Real registered lobbying totals (Senate LDA filings) now accompany matches where they exist.",
      "Promise evaluations are cleaned before scoring, so the Promise Persistence score is computed from exactly the promises shown.",
      "A funding-window ordering bug was fixed that gave one senator his 1984 and 2014 campaign totals instead of his most recent race.",
    ],
  },
  {
    version: "v4",
    date: "2026-07-03",
    title: "Score audit overhaul",
    changes: [
      "Funding Independence rebuilt: PAC dependency (50%), small-donor share (25%), relative top-donor concentration (25%). The previous concentration metric was structurally near-zero for $50M+ campaigns, so missing PAC data scored as independence.",
      "PAC totals now come from authoritative FEC cycle totals instead of classifier-typed donor sums; outside spending uses complete per-cycle Schedule E totals (previously truncated to the 50 largest expenditures).",
      "Independent Voting: donor component reduced to 25% while alignment data is unavailable, and no longer a constant; the party-break curve gained a 3% base rate and a capped safe-state discount.",
      "Legislative Effectiveness: advancement now counts substantive bills only (no commemorative resolutions, no double-counted laws, no calendar placements); volume is per-congress. The previous version saturated at a mean of 82.",
      "Promise alignment: sponsoring a bill on a promised topic now counts as effort (partial at most) — only advanced legislation counts as kept; topically-related votes with unknown direction no longer count as kept.",
      "House party-loyalty labels now defer to actual roll-call splits instead of content classification, fixing House Independent Voting scores that were pinned at ~87 for all 431 representatives.",
    ],
  },
  {
    version: "v3",
    date: "2026-05-15",
    title: "Data quality audit",
    changes: [
      "Funding Independence reweighted toward donor concentration; Schedule E outside spending added.",
      "Promise Persistence decoupled from voting data (removed circular fallback).",
      "Independent Voting donor default scaled by fundraising total.",
      "Legislative Effectiveness advancement threshold recalibrated to actual Senate passage rates.",
    ],
  },
];
