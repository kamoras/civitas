import { coverageSentence, formatAsOf } from "@/lib/electionMap";
import type { ElectionMapData } from "@/types/electionMap";

const pct = (x: number) => `${Math.round(x * 100)}%`;

/** "How this is measured": the method, its inputs and licences, and the
 * evidence that its ranges are honest — all figures from the API. */
export default function ElectionMapMethod({ data }: { data: ElectionMapData }) {
  const c = data.calibration;
  const [b0, bLean, bInc, bNat] = data.model.beta;
  return (
    <section
      id="method"
      aria-labelledby="method-heading"
      className="scroll-mt-[var(--header-clearance)] space-y-4 text-[15px] leading-relaxed text-ink-lo"
    >
      <h2 id="method-heading" className="font-display text-2xl font-bold text-ink-hi">
        How this is measured
      </h2>
      <p>
        This map is not a prediction and calls no race. It shows how each seat usually votes and,
        where both parties have a nominee, the range the vote margin has tended to fall in when the
        usual vote and this year&apos;s polls looked like this. It is separate from Civitas&apos;s
        Representation Scores: nothing here is a score of anyone, and nothing here uses one.
      </p>

      <h3 className="font-display text-lg font-bold text-ink-hi">The three views</h3>
      <ul className="list-disc space-y-1 pl-5">
        <li>
          <strong className="text-ink">Usual vote</strong> is each state&apos;s lean: how much more
          Republican or Democratic it voted than the nation in the 2020 and 2024 presidential
          elections (Cook&apos;s formula, 75% on the newer one), computed by Civitas from official
          returns. No model is involved.
        </li>
        <li>
          <strong className="text-ink">Polls</strong> is an 80% range for the margin: the usual
          vote, plus incumbency and the national generic-ballot average, combined with the
          seat&apos;s own polls. A seat is coloured by the end of its range nearest even, so a range
          that crosses even is grey and labelled too close to tell.
        </li>
        <li>
          <strong className="text-ink">Difference</strong> marks seats whose polls pull the range
          clear of where the usual vote, incumbency and the national swing alone would put it: one
          mark when that point is outside the 80% range, two when outside the 95% range.
        </li>
      </ul>

      <h3 className="font-display text-lg font-bold text-ink-hi">The method</h3>
      <ol className="list-decimal space-y-2 pl-5">
        <li>
          <strong className="text-ink">Structural line.</strong> A straight-line fit through every
          contested Senate general election from {c.cycles[0]} to {c.cycles[1]}: margin ={" "}
          {b0.toFixed(1)} + {bLean.toFixed(2)} × lean + {bInc.toFixed(1)} × incumbent +{" "}
          {bNat.toFixed(2)} × national. The incumbent term is +1 for a Republican incumbent, −1 for
          a Democrat, 0 for an open seat; national is the generic-ballot average. A seat with no
          poll keeps this line, with its historical spread ({data.model.priorSd.toFixed(1)} points),
          which is why those ranges are wide.
        </li>
        <li>
          <strong className="text-ink">Poll average.</strong> Each pollster&apos;s latest poll from
          the last {data.pollWindowDays} days, weighted by recency (half-life {data.model.halfLife}{" "}
          days) and the square root of its sample, after removing the pollster&apos;s measured house
          effect, or for a pollster with no record, the measured lean of polls the source marks as
          party-sponsored. Sponsored polls are flagged, not hidden.
        </li>
        <li>
          <strong className="text-ink">Combination.</strong> The two are averaged by inverse
          variance, so a race with many recent polls is mostly polls and a race with none is all
          structure. One scale factor ({data.model.scale.toFixed(2)}) is fitted so the ranges hold
          the result as often as they say.
        </li>
      </ol>
      <p>
        Every number in these formulas is fitted by a script from the sources below and written to{" "}
        <code className="font-mono text-sm">{data.model.file}</code> ({data.model.fit}); none is
        typed by hand.
      </p>

      <h3 className="font-display text-lg font-bold text-ink-hi">Is it honest? Held-out checks</h3>
      <p>
        Each cycle from {c.cycles[0]} to {c.cycles[1]} was predicted by a fit that never saw it,
        using only the polls fielded by {data.horizon} days before election day.{" "}
        {coverageSentence(c)} The 95% ranges held it {pct(c.coverage95)} of the time. The middle of
        the range missed the margin by {c.meanAbsError} points on average.
      </p>
      <p>
        Internally the ranges imply a chance for each race; the map never shows one, because a
        chance reads as a call. This table is how those chances were checked: of the races the
        method put in each band, how many went to the Democratic candidate.
      </p>
      <div
        className="overflow-x-auto"
        tabIndex={0}
        role="region"
        aria-label="Held-out calibration table"
      >
        <table className="w-full min-w-[30rem] border-collapse font-mono text-sm">
          <caption className="sr-only">
            Held-out calibration of the implied chances, {c.races} races
          </caption>
          <thead className="border-b border-white/20 text-left text-xs uppercase tracking-[0.12em] text-ink-min">
            <tr>
              <th scope="col" className="px-2 py-1">
                Band
              </th>
              <th scope="col" className="px-2 py-1">
                Races
              </th>
              <th scope="col" className="px-2 py-1">
                Average stated
              </th>
              <th scope="col" className="px-2 py-1">
                Went to the Democrat
              </th>
            </tr>
          </thead>
          <tbody>
            {c.table.map((b) => (
              <tr key={b.low} className="border-b border-white/[0.07]">
                <th scope="row" className="px-2 py-1 text-left font-normal">
                  {pct(b.low)}–{pct(b.high)}
                  {b.low === 0.35 ? " (near even)" : ""}
                </th>
                <td className="px-2 py-1">{b.n}</td>
                <td className="px-2 py-1">
                  {b.meanChance !== undefined ? pct(b.meanChance) : "–"}
                </td>
                <td className="px-2 py-1">
                  {b.demShare !== undefined
                    ? `${pct(b.demShare)} (${b.demCarried} of ${b.n})`
                    : "–"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p>
        On the {c.brier.polledRaces} held-out races with polls, the Brier score (lower is better)
        was {c.brier.combined.toFixed(3)} for this method, {c.brier.pollsOnly.toFixed(3)} for the
        poll average alone and {c.brier.fundamentals.toFixed(3)} for the structural line alone.
      </p>

      <h3 className="font-display text-lg font-bold text-ink-hi">Why there is no seat total</h3>
      <p>
        Adding up races only works if their errors are independent, and they are not: when polls
        miss, they tend to miss the same way everywhere. In {c.seatTotalsOutside90} of the{" "}
        {c.seatTotals.length} held-out cycles the number of seats Democrats carried fell outside the
        90% band that independent races would give. Until a shared national error is modelled and
        checked the same way, the map shows each seat&apos;s own range and no tally by party.
      </p>

      <h3 className="font-display text-lg font-bold text-ink-hi">Inputs, sources and licences</h3>
      <ul className="list-disc space-y-1 pl-5">
        <li>
          Usual vote: {data.sources.lean.source} (
          <code className="font-mono text-sm">{data.sources.lean.file}</code>
          ).
        </li>
        <li>Training: {data.sources.training}</li>
        <li>
          This year&apos;s polls and the generic-ballot averages: Wikipedia&apos;s 2026 race
          articles, read as of each article&apos;s revision shown with its seat. Every table passes
          checks before it is used: dates on or before the revision, shares that add up, no poll
          listed twice, one column per nominee. A revision that fails keeps the previous good one.
        </li>
        <li>
          Generic ballot: the average of the published averages Wikipedia lists (now{" "}
          {data.national.label},{" "}
          <a
            href={data.national.url}
            className="underline decoration-white/30 hover:text-ink-hi"
            rel="noopener noreferrer"
          >
            revision {data.national.revid}
          </a>
          ). The fit used the average of individual generic-ballot polls; Wikipedia lists averages,
          not polls.
        </li>
        <li>
          Licence: {data.sources.polls.notice} (
          <a
            href={data.sources.polls.licenseUrl}
            className="underline decoration-white/30 hover:text-ink-hi"
            rel="noopener noreferrer"
          >
            {data.sources.polls.license}
          </a>
          ).
        </li>
      </ul>
      <p>
        Only Democrat-versus-Republican races are ranged, because only those were in the fit. A seat
        with no Democratic or Republican nominee, or where a candidate of neither party polls ahead
        of a major-party nominee, shows its usual vote and says why it has no range. House districts
        are not on this map yet. As of {formatAsOf(data.asOf)}; {data.daysToElection} days to
        election day. From election day on, the results map takes this map&apos;s place.
      </p>
    </section>
  );
}
