# Presidential Effectiveness and Agency Alignment

This note records why president v5 changed how economic growth, jobs and
rulemaking are scored. The numbers are printed by
[`backend/scripts/research_president_scores.py`](../../backend/scripts/research_president_scores.py),
which runs this repository's own `compute_term_gdp_growth` over public data.
Run with `--write-fallback`, it also writes the bundled pre-first-run
statistics.

**Data.**
- **GDP:** the Maddison Project's US real GDP (GDP per capita × population),
  through 2016, and the same series for 13 other advanced economies.
- **Employment:** the BLS household survey, 1941–2025.
- **Rulemaking:** published Federal Register counts from the Competitive
  Enterprise Institute and GWU's Regulatory Studies Center.

## GDP growth: compare within its era

The old score was a fixed line, 25 + growth/5 × 55, built around a
"post-WWII average of 3.2%" and applied to every president back to 1789.

| Presidencies | Mean term growth | SD | Pinned at 0 or 100 by the old line |
|---|---|---|---|
| Before 1947 (n=25) | 3.32% | 2.87 | 12% |
| 1947 on (n=9) | 2.84% | 1.13 | 0% |

Before 1947, term growth varies 2.5 times as much. Part of that is
measurement: prewar GDP estimates overstate cyclical volatility by
construction (Romer 1989, "The Prewar Business Cycle Reconsidered", *JPE*
97(1)). **Changed:** growth is now scored against the mean and SD of
presidents in the same data regime, measured every run.

## Most of a modern president's growth is shared with other economies

US term growth against the median growth of 13 other advanced economies over
the same years (Britain, France, Germany, the Netherlands, Canada, Australia,
Sweden, Italy, Belgium, Denmark, Switzerland, Norway, Japan):

| Presidencies | r | Shared variance |
|---|---|---|
| Before 1947 | 0.46 | 21% |
| 1947 on | 0.77 | **60%** |

This is the empirical core of Blinder & Watson (2016). The US growth gap
between administrations mostly reflects oil shocks, productivity and global
conditions rather than policy. Scoring growth *relative to peer economies*
would remove the shared component.

**Built in president v8**, below.

## Jobs: absolute, against a measured average

| Measure, presidencies 1945–2025 (n=12) | Spearman with term start year |
|---|---|
| Jobs per year (absolute) | 0.01 (p=0.98) |
| Percent employment growth per year | −0.52 (p=0.08) |

The percentage rate falls with the labor force's own slowing growth, a
demographic trend. Switching to it would penalize recent presidents for
demographics. **Kept absolute.** It is now scored against the measured mean
and SD of presidencies since 1939, replacing the fixed "3 million a year".

## Rulemaking volume measured regulatory philosophy

Agency Alignment's activity component scored more rules per year as better.
Annual counts follow each administration's regulatory stance. The Federal
Register's final-rule count hit its lowest level on record in 2019 (2,964),
and its final-rule page count its highest in 2024 (CEI, *Ten Thousand
Commandments* 2025; GWU Regulatory Studies Center, RegStats). **Removed.**
Agency Alignment is now the share of rulemaking documents that were final
rules, scored against the administrations since 1994. It is measured from as
few as five administrations, because that is the whole population with
machine-readable data.

## Approval trend: judged against where the term started (President v6)

Public Mandate's trend component (30%) is the last quarter's average approval
minus the first quarter's. Until v6 it was z-scored against the average trend
of every president with polling, -14.2 points. Two things made that unfair.

**The starting level sets the room to move.** Approval is bounded, and the
honeymoon fades from wherever it began. Across the 14 completed polling-era
presidencies (UCSB American Presidency Project, 2026-10-01), the starting
level (first-quarter average) explains 45% of the trend's variance:
trend = 44.0 - 0.99 x start (r = -0.67, residual SD 11.9): a president who
started 10 points higher went on to fall about 10 points further. Scored against the flat average, a
president who began near the floor looked steady for having nowhere to fall.
The trend is now scored against the fitted expectation for the president's
own start, with the residual SD as the scale (`fit_trend_on_start`), refitted
every run. With 14 presidencies the fit is noisy, but the relationship is
strong (p < 0.01) and its sign is what bounded measures predict.

**The sitting president's term is partial.** It was compared with whole
terms. Approval falls as a term goes on: the completed presidencies averaged
57.5% over their first 598 days and 51.9% over their full terms. So a short
window flattered the sitting president on both components, and the partial
term also sat inside the population everyone else was scored against. Now
the population is completed presidencies only, and the sitting president is
compared with each predecessor over the same number of days from their first
poll (`sitting_window_reference`; a presidency shorter than the window is
left out). Within that window the start still explains much of the change
(r = -0.52).

**Effect** (live data, 2026-10-01): the sitting president's Public Mandate
falls by about half, since a modest decline from a low start had been read as
better than average; completed presidencies that started high and held their
approval rise by 5 to 11 points; most others move down a few points, because the
sitting president's partial-term average no longer lowers the comparison
average.

## Also fixed

The score breakdown preferred a stored `gdp_growth_adjusted` column that
nothing had written since GDP growth moved to `compute_term_gdp_growth`
(which already excludes year 1). "Show the math" could therefore use a
stale figure different from the one scored. The model no longer reads or writes it; the column itself is dropped in the release after this one, because the previous image still reads it (backend/migrations/README.md).

## Limits

- The research GDP series (Maddison) and jobs series (household survey) are
  proxies for the pipeline's own MeasuringWorth and BLS payroll series. They
  serve only as the pre-first-run fallback, and the first run replaces them.
- The postwar comparison rests on 9–14 presidencies. That is the whole
  population, and it is small.

## President v7: rulemaking counts were capped; a term cut short

**Agency Alignment removed.** The Federal Register's documents API reports
`count` as at most 10,000 (final rules over 2009-2017 read 10,000; each year
alone reads about 3,500 to 3,900, checked 2026-10-06). The two-term
administrations' final and proposed rule counts were both capped, so each read
exactly 50% finalized. Counted in full (windows halved until under the cap),
administrations since 1994 finalized between 59.6% (Obama) and 61.8% (George W.
Bush), too little difference to score.

**A presidency cut short.** Kennedy (1,036 days) and Ford (895) were compared
with whole terms, when approval falls as a term goes on. From a start of 76%,
presidents fell about 30.5 points over a whole term but 16.1 over their first
1,001 days; Kennedy fell 14. Compared over his own days, his approval trend
scores 57 rather than 87. Both are left out of the whole-term population.

## President v8: growth relative to peer economies, catch-up set aside

**Sources.** Maddison Project Database 2023 (Bolt & van Zanden 2024; GDP
per person, 2011 international dollars at purchasing-power parity) for the
US and the 13 peers, 1946-2022, bundled as
`backend/app/data/peer_gdp_per_capita.json` by
`backend/scripts/fetch_peer_gdp.py`; the World Bank's NY.GDP.PCAP.KD
(constant 2015 US$) carries those levels past 2022. Where both exist the
two series' annual growth rates agree (r = 0.90, 868 country-years
1961-2022, mean difference 0.16 points).

**How much is shared.** Over the years Effectiveness credits (the term's
second year to its last), the peers' median growth per person explains 68%
of the variance in a postwar term's US growth per person (World Bank,
1961 on). Counting the first year too: 49%. A window shifted a year either
way: 54%. The credited window is the one that lines up best.

**Inherited momentum.** Growth in the two years before a term does not
predict growth during it (r = -0.08 over 43 terms, MeasuringWorth), so
nothing beyond the year-1 exclusion is carried for the predecessor.

**Catch-up.** Compared raw, US-minus-peers growth rises with a term's
start year: r = +0.73 over the 13 completed postwar terms (data through
2025). In the 1950s and 1960s the peers were far below US incomes (median
log gap -0.47 in 1950, -0.29 in 1990, -0.22 in 2022) and growing fast by
catching up (Baumol 1986; Barro & Sala-i-Martin 1992). Three ways of
setting that aside were measured:

| Method | Fitted rate | Era trend r | Problem |
|---|---|---|---|
| None (raw) | - | +0.73 | penalizes the 1950s-60s |
| Each peer-year's growth on its own gap, pooled | -3.3 to -5.3 by sample | +0.34 | rate unstable; over-credits every recent term |
| Each term's peers regressed on their gaps | per term | -0.42 | follows noise: shared variance with US growth falls from 0.49 to 0.02 |
| Each year's US-minus-peer-median on the peers' median gap, least squares | 5.6 to 10.2 by first year 1947-1955 | -0.06 to +0.26 | one reconstruction year swings it |
| Same, Theil-Sen (median of pairwise slopes; Sen 1968) | 7.1 to 9.9 | -0.03 to +0.17 | chosen |

(Era trends in the table are Maddison data through 2022, where Biden's term
has one year.) The pipeline fits the Theil-Sen rate every run over every
year since 1947 (8.9 in October 2026). With data through 2025 the adjusted
figure's era trend is +0.41, all of it from the terms since 2017, when US
growth per person ran 0.9 to 1.1 points a year ahead of the peers'; that is
an outcome, not something the adjustment introduced.

**Party.** Democratic minus Republican: total growth +0.92 points a year,
growth per person +0.98, relative to peers raw +0.56, adjusted +0.56
(13 completed postwar terms, data through 2025).

**Effect on Effectiveness (October 2026 data, DB copy).** Eisenhower 36 to
18, Kennedy 69 to 48, Johnson 87 to 76, Nixon 45 to 26, Ford 62 to 59,
Carter 50 to 58, Reagan 69 to 70, George H. W. Bush 21 to 31, Clinton 73
to 55, George W. Bush 20 to 28, Obama 44 to 49, Trump (first term) 20 to
45, Biden 53 to 83. Prewar presidents are unchanged.

**Limits.** The adjustment removes shocks the peers shared and their
catch-up, not shocks that hit the US alone. Jobs (40% of Effectiveness)
are still US payroll jobs per year, with no peer comparison.

## President v9: approval by party, against the era's polarization

**Question.** Average approval is tightly bunched (SD 9.1 points across
the 14 completed polling-era presidencies), so a few points cost a lot of
score. Is the spread signal?

**Measurement precision is not the problem.** Splitting each president's
polls into alternating halves, the halves' averages agree at r = 0.999
(overall), 0.997 (own party), 0.998 (other party), 0.996 (independents).
The differences are real; the question is what they measure.

**Era is.** Approval by party (Gallup, via the American Presidency
Project, every president from Truman) against the distance between the
parties' mean DW-NOMINATE scores in the House over the term (Voteview;
McCarty, Poole & Rosenthal 2006):

| Measure | SD | r with polarization |
|---|---|---|
| Overall approval | 9.1 | -0.40 |
| President's own party | 10.8 | +0.55 |
| Other party | 14.3 | **-0.81** |
| Independents | 10.2 | -0.46 |

Other-party approval fell from 49% (Eisenhower) to 5.5% (Biden) while
own-party approval rose; Jacobson (2019) and Donovan, Kellstedt, Key &
Lebo (2020) document the same shift. Raw approval ranked presidents
partly by when they served.

**Two adjustments measured.**

| Method | Slope range, leaving one presidency out | Largest move in any president's figure |
|---|---|---|
| Overall approval on polarization | -17 to -36 | 5.2 points |
| Each group on polarization, gaps averaged | other party -80 to -98 | **2.5 points** (spread 8.5) |

The single fit can't be estimated from 14 presidencies; the group fits can,
because the other party's relationship is strong. Chosen: each group's
approval minus what the group gave presidents under the same polarization
(Theil-Sen fits, refitted every run), averaged over the three groups. Its
own correlation with polarization is +0.16 and with raw approval +0.80.

**Trend.** The approval trend, judged against the starting level (v6),
shows no era relationship (r = -0.06 with polarization), so it is
unchanged.

**Effect (October 2026 data, DB copy).** Public Mandate: Truman 17 to 8,
Kennedy 79 to 67, Johnson 67 to 47, Eisenhower 92 to 90, Carter 37 to 26,
Reagan 61 to 65, George H. W. Bush 75 to 73, George W. Bush 46 to 55,
Obama 46 to 54, Trump (first term) 21 to 40, Biden 24 to 38. Top 10
overall: Reagan 7th to 5th, Johnson 5th to 9th, Kennedy 6th to 8th.

**Also found.** Voteview names per-Congress files with three digits
(H099); the fetcher's URL template wrote H99, a 404, for every Congress
before the 100th. Only the sitting Congress had been read before.

