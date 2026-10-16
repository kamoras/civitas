import { chamberWord, coverageSentence, formatAsOf, leanWindowWords } from "@/lib/electionMap";
import type { ElectionMapData } from "@/types/electionMap";

const pct = (x: number) => `${Math.round(x * 100)}%`;

/** "How this is measured": the method, its inputs and licences, and the
 * evidence that its ranges are honest — all figures from the API. */
export default function ElectionMapMethod({ data }: { data: ElectionMapData }) {
  const c = data.calibration;
  const [b0, bLean, bInc, bNat] = data.model.beta;
  const house = data.chamber === "house";
  return (
    <section
      id="method"
      aria-labelledby="method-heading"
      className="space-y-4 text-[15px] leading-relaxed text-ink-lo"
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
          <strong className="text-ink">Usual vote</strong> is each {house ? "district" : "state"}
          &apos;s lean: how much more Republican or Democratic it voted than the nation in the{" "}
          {leanWindowWords(data.sources.lean.window)} presidential elections (Cook&apos;s formula,
          75% on the newer one), computed by Civitas from{" "}
          {house
            ? `precinct returns placed on the ${data.cycle} district lines`
            : "official returns"}
          . No model is involved. Written D+N or R+N, it is N points of the two-party vote: the
          party&apos;s share here minus its share nationwide.
        </li>
        <li>
          <strong className="text-ink">Polls</strong> is an 80% range for the margin: the usual
          vote, plus incumbency and the national generic-ballot average, combined with the
          seat&apos;s own polls. A seat is coloured by the end of its range nearest even, so a range
          that crosses even is grey and labelled too close to tell. The margin is the Republican
          share of the two-party vote minus the Democratic share, also written D+N or R+N but on
          twice the lean&apos;s scale: a gap between two shares, where a lean is one share against
          the nation&apos;s. A seat leaning D+8 has a margin of D+16 when the nation splits evenly,
          so a margin printed beside a lean is not a swing. Each range is labelled by what it rests
          on: <em>from polls</em> when the seat&apos;s own polls are in the average,{" "}
          <em>no poll of this seat</em> when none is (the structural line alone), and{" "}
          <em>before polls count</em> further out than the poll window.
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
          contested {chamberWord(data.chamber)} general election from {c.cycles[0]} to {c.cycles[1]}
          : margin = {b0.toFixed(1)} + {bLean.toFixed(2)} × lean + {bInc.toFixed(1)} × incumbent +{" "}
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
          party-sponsored. Sponsored polls are flagged, not hidden. The {data.pollWindowDays} days
          are not chosen: they are how far before election day the past polls the method is fitted
          on reach, so it is never checked on older polls. Further out than that the map shows the
          structural line alone, labelled fundamentals only, with each seat&apos;s polls listed
          beside it{data.pollsInRange ? "" : " (as now)"}.
        </li>
        <li>
          <strong className="text-ink">Combination.</strong> The two are averaged by inverse
          variance, so a race with many recent polls is mostly polls and a race with none is all
          structure. One scale factor ({data.model.scale.toFixed(2)}) is fitted so the ranges hold
          the result as often as they say.
        </li>
      </ol>
      {house && (
        <p>
          A House fit needs each district&apos;s lean as it stood before each past election, on the
          lines that election used. Civitas rebuilt it the same way as the usual vote, from VEST
          precinct returns placed on Census blocks: for 2018 and 2020 from the 2016 presidential
          vote alone, on the 116th Congress&apos;s lines (2012 has no open precinct returns), and
          for 2022 from 2016 and 2020 on the new lines drawn after the census. North Carolina&apos;s
          2020 races are left out (it voted on new lines Census publishes only on older blocks). A
          cycle is added once the FEC publishes its compilation of that election&apos;s House
          results (MEDSL&apos;s House file sits behind a sign-in form, which Civitas does not
          answer), so the House checks cover {c.cycles[0]} to {c.cycles[1]}.
        </p>
      )}
      <p>
        Every number in these formulas is fitted by a script from the sources below and written to{" "}
        <code className="font-mono text-sm">{data.model.file}</code> ({data.model.fit}); none is
        typed by hand.
      </p>

      <h3 className="font-display text-lg font-bold text-ink-hi">Is it honest? Held-out checks</h3>
      <p>
        Each cycle from {c.cycles[0]} to {c.cycles[1]} was predicted by a fit that never saw it,
        using only the polls fielded by {data.horizon} days before election day.{" "}
        {coverageSentence(c, data.chamber)} The 95% ranges held it {pct(c.coverage95)} of the time.
        The middle of the range missed the margin by {c.meanAbsError} points on average.
      </p>
      {c.forward && (
        <p>
          The hardest check is the newest cycle, {c.forward.cycle}, predicted by a fit on{" "}
          {c.forward.trainedOn[0]} to {c.forward.trainedOn[1]} only, so nothing from{" "}
          {c.forward.cycle} or later reached it. Its 80% ranges held the result{" "}
          {pct(c.forward.coverage80)} of the time in {c.forward.races} races (for that many races,
          honest 80% ranges land between {pct(c.forward.coverage80Band[0])} and{" "}
          {pct(c.forward.coverage80Band[1])} in 95 cycles of 100), and its 95% ranges{" "}
          {pct(c.forward.coverage95)}. On its {c.forward.polledRaces} races with polls the Brier
          score was {c.forward.brier.combined.toFixed(3)}, against{" "}
          {c.forward.brier.pollsOnly.toFixed(3)} for the poll average alone and{" "}
          {c.forward.brier.fundamentals.toFixed(3)} for the structural line alone.
        </p>
      )}
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
        {data.sources.trainingPolls && (
          <li>
            Past polls:{" "}
            <a
              href={data.sources.trainingPolls.url}
              className="underline decoration-white/30 hover:text-ink-hi"
              rel="noopener noreferrer"
            >
              {data.sources.trainingPolls.attribution}&apos;s raw poll file
            </a>
            , every rated poll since 1998 with its certified result, used free of charge as its
            terms allow and attributed to {data.sources.trainingPolls.attribution} as they ask
            {data.sources.trainingPolls.terms ? ` ("${data.sources.trainingPolls.terms}")` : ""}. A
            new copy is used only if it passes checks: the terms are unchanged, the columns are the
            same, no poll already used has gone or changed beyond a few corrections, every poll ends
            before its election and within the window, shares add up, a new cycle has Senate and
            generic-ballot polls, and its stated results agree with the MIT Election Lab&apos;s
            certified counts. The fit refits itself when a new copy passes.
          </li>
        )}
        <li>
          This year&apos;s polls and the generic-ballot averages: Wikipedia&apos;s {data.cycle} race
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
        of a major-party nominee, shows its usual vote and says why it has no range. As of{" "}
        {formatAsOf(data.asOf)}; {data.daysToElection} days to election day. From election day on,
        the results map takes this map&apos;s place.
      </p>
    </section>
  );
}
