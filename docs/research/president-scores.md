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

**Not built yet, and why.** The pipeline has no live source for other
countries' GDP. The candidates are the Maddison Project, the World Bank API
and the OECD API. None could be reached from the environment this work was
done in, so the pipeline would have been built against a format nobody had
checked. The limitation is stated on the About page and in the Effectiveness
docstring instead.

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

## Also fixed

The score breakdown preferred a stored `gdp_growth_adjusted` column that
nothing had written since GDP growth moved to `compute_term_gdp_growth`
(which already excludes year 1). "Show the math" could therefore use a
stale figure different from the one scored. The column is dropped.

## Limits

- The research GDP series (Maddison) and jobs series (household survey) are
  proxies for the pipeline's own MeasuringWorth and BLS payroll series. They
  serve only as the pre-first-run fallback, and the first run replaces them.
- The postwar comparison rests on 9–14 presidencies. That is the whole
  population, and it is small.
