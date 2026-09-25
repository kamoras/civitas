# Funding Independence: counting each signal once

This note records why v6.13 removed two of Funding Independence's inputs and
one fallback. Each was checked against FEC's own data rather than argued.
Every number is printed by
[`backend/scripts/audit_funding_components.py`](../../backend/scripts/audit_funding_components.py),
which downloads FEC's bulk files and runs every check.

**Data.** FEC candidate summaries, independent-expenditure files and committee
master files for the 2020, 2022 and 2024 elections. The sample is incumbents
who raised more than $100K: 1,276 in total. The seat-lean analyses use the
415 House incumbents in 2024, matched to current district lines, and the 86
senators whose election fell in 2020–2024. Shares are taken over
contributions plus candidate loans, the same base the score uses.

## 1. Outside spending measured race competitiveness, not dependency

Before v6.13, independent expenditures supporting a member were added to their
PAC share at half weight, as "aligned-industry investment in the seat".

Who spent the $711M supporting these incumbents:

| Spender | Share |
|---|---|
| Super PACs | 72.0% |
| Hybrid PACs | 14.0% |
| Traditional PACs | 7.8% |
| Non-committee groups, individuals | 3.7% |
| Party committees | 2.5% |

The largest spenders are a mix: ideological (Americans for Prosperity Action
5.1%, LCV Victory Fund 3.1%, Club for Growth Action 2.0%), industry (National
Association of Realtors 4.4%, Fairshake 3.6%) and party-aligned (SMP 2.5%).
"Industry investment" describes only part of it.

Where it goes, and what the old term did to the PAC component:

| Seat lean | House n | House: term (mean) | House: points off PAC component | Senate n | Senate: term (mean) | Senate: points off PAC component |
|---|---|---|---|---|---|---|
| Within 3 | 56 | 0.069 | 7.7 | 19 | 0.084 | 28.4 |
| 4–8 | 91 | 0.026 | 2.9 | 26 | 0.034 | 11.5 |
| 9–15 | 126 | 0.010 | 1.1 | 30 | 0.036 | 12.0 |
| Beyond 15 | 142 | 0.010 | 1.1 | 11 | 0.025 | 8.4 |

- **Competitiveness:** Spearman ρ between the term and |PVI| is −0.37 in the
  House and −0.39 in the Senate (both p<0.001).
- **PAC dependency:** the term runs *opposite* to PAC share (ρ = −0.28 House,
  −0.32 Senate). Members in competitive seats raise less of their money
  from PACs, and more outside money arrives on their behalf.
- **Control:** independent expenditures are by law not coordinated with the
  candidate (52 U.S.C. §30101(17)).
- **Literature:** super PACs concentrating in competitive races is also
  documented directly (Scala 2021, "Are Super PACs Super-Efficient?",
  *State of the Parties*).

The term penalized swing-seat members for money they cannot solicit or
direct. **Removed**, together with the FEC fetch that only it used.

## 2. Source breadth was the small-donor share again

Breadth scored small-donor money at 1.0, industry-classified money at 0.6,
unclassified at 0.5 and everything else at 0.2. The industry breakdown
covers itemized individual and PAC money. The share of that the classifier
assigns an industry can't be observed in FEC data, so three plausible values
are shown:

| Classified share | corr(small-donor share, breadth) | R² |
|---|---|---|
| 0.5 | 0.928 | 0.862 |
| 0.8 | 0.907 | 0.823 |
| 1.0 | 0.891 | 0.793 |

Algebraically, with the four shares summing to one, breadth is
0.6 + 0.4 × small-donor share, minus terms for unclassified and "opaque"
money. The small-donor share already has its own component, so breadth
mostly re-weighted it.

What breadth added beyond that was a penalty on "opaque" money, which is
party money and the candidate's own. Self-funding is the one source no donor
can influence, so penalizing it runs against the dimension's purpose. It
affects few members: 38 of 1,276 incumbents were at least 5% self-funded,
and 16 were at least 25%. **Removed.**

## 3. The industry-concentration fallback was a third copy

When too little money is industry-classified to measure concentration, the
component fell back to "50 + 50 × small-donor share". It also blended toward
that value when 5–40% of money was classified. **Changed:** Funding
Independence now falls back to a neutral 50, like every other missing input.
Funding Diversity, which is displayed but not part of the overall score,
keeps its grassroots fallback as its own standalone description.

## Weights

The four remaining components keep their pre-v6.13 proportions (20:10:10:13),
renormalized: PAC dependency 20/53, small-donor share 10/53, top-donor
concentration 10/53, industry concentration 13/53. No new weighting judgment
was made. The data here says what to remove, not how to reweigh what stays.

## Limits

- Industry classification rates can't be observed in FEC data. The breadth
  result is shown across the plausible range and holds throughout it.
- House seat lean uses current district lines, so the House analysis is
  2024 only.
- This is FEC's candidate-level data, not Civitas's own pipeline output. The
  shares match the score's definitions, but the classified-industry pieces
  are approximations, as stated above.
