import Link from "next/link";
import type { BallotCandidate, FundingSourceKey, FundingSources } from "@/types/election";
import { SERIES } from "@/components/admin/charts/palette";
import { cashOnHandDisplay, displayScore, formatCurrency } from "@/lib/formatting";
import { getScoreColor } from "@/lib/representation";
import { getPartyMeta } from "@/components/elections/CandidateCard";
import {
  candidateName,
  fecCandidateUrl,
  incumbencyLabel,
  memberRecordLabel,
  raisedLabel,
} from "@/lib/elections";

/**
 * A race as a comparison, not a list.
 *
 * Money raised is the most decision-relevant published fact this page
 * has about a candidate, and it rendered as a number in a stack of
 * cards — so telling a $1.9M campaign from a $130K one meant reading
 * and dividing. Measured on the live data, Georgia's House races run
 * 10:1 to 15:1 between the two candidates; that gap is the story and it
 * should be visible before it is read.
 *
 * Bars are scaled to the LEADER of this race, not to any national
 * figure: the question a voter is asking is "who is out-raising whom
 * here", and a scale that made a safe seat look empty next to a
 * battleground would answer a different one.
 *
 * Sorted by contributions descending. In a state still showing FEC
 * filers that ordering is doing real work — a 25-filer race puts the
 * people running actual campaigns first instead of leaving them in
 * whatever order the roster returned.
 *
 * `redrawnSeat`: a House seat on new district lines, where the member
 * going into the election is not this district's incumbent
 * (incumbencyLabel); `resultsMode` (from election day on) words them
 * without claiming they still sit.
 */
/** The FEC's categories of contributions, in the backend's order, each with
 * an admin-palette series colour. The order is the palette's starting at its
 * second colour — a rotation that still clears the dataviz validator's
 * lightness, chroma, colour-vision and contrast gates on the page surface —
 * so no category opens on the blue that sits closest to a party's. */
const FUNDING: Record<FundingSourceKey, { label: string; color: string }> = {
  smallDonors: { label: "donors of $200 or less", color: SERIES[1] },
  largerDonors: { label: "donors of more than $200", color: SERIES[2] },
  committees: { label: "PACs and other committees", color: SERIES[3] },
  party: { label: "party committees", color: SERIES[4] },
  candidate: { label: "the candidate", color: SERIES[0] },
};

/** Which colour is which, once for the race rather than on every row. */
function FundingLegend() {
  return (
    <div className="mb-1 border-b border-white/[0.07] pb-3">
      <p className="font-mono text-[11px] uppercase tracking-[0.14em] text-ink-min">
        Where contributions came from · 1 block = 5%
      </p>
      <ul className="mt-1.5 flex flex-wrap gap-x-4 gap-y-1 text-[12px] text-ink-lo">
        {Object.entries(FUNDING).map(([key, f]) => (
          <li key={key} className="flex items-center gap-1.5">
            <span className="h-2 w-2" style={{ backgroundColor: f.color }} aria-hidden="true" />
            {f.label}
          </li>
        ))}
      </ul>
    </div>
  );
}

/** One candidate's contributions as 20 blocks in the FEC's categories, with
 * the shares in words beside it so colour never carries them alone. Every
 * share and block count is the backend's (race_context.funding_sources). */
function FundingStrip({ sources }: { sources: FundingSources }) {
  if (!sources.addsUp) {
    return (
      <p className="mt-2 text-[12px] text-ink-min">
        No split shown: the categories in this FEC filing don&apos;t add up to its total.
      </p>
    );
  }
  const shown = sources.parts.filter((p) => (p.percent ?? 0) > 0);
  return (
    <div className="mt-2">
      <div className="flex gap-[2px]" aria-hidden="true">
        {sources.parts.flatMap((p) =>
          Array.from({ length: p.blocks ?? 0 }, (_, i) => (
            <span
              key={`${p.key}-${i}`}
              className="h-2 flex-1"
              style={{ backgroundColor: FUNDING[p.key].color }}
            />
          ))
        )}
      </div>
      <p className="mt-1 text-[12px] leading-snug text-ink-lo">
        {shown.map((p) => `${p.percent}% from ${FUNDING[p.key].label}`).join(" · ")}
      </p>
    </div>
  );
}

export default function RaceMoneyBars({
  candidates,
  cycleYear,
  showUnconfirmed = false,
  redrawnSeat = false,
  resultsMode = false,
}: {
  candidates: BallotCandidate[];
  /** The race's cycle: which election's figures the FEC link opens. */
  cycleYear: number;
  showUnconfirmed?: boolean;
  redrawnSeat?: boolean;
  resultsMode?: boolean;
}) {
  const ranked = [...candidates].sort((a, b) => (b.contributions ?? 0) - (a.contributions ?? 0));
  const leader = ranked[0]?.contributions ?? 0;
  const anySplit = ranked.some((c) => c.fundingSources?.addsUp);

  return (
    <>
      {anySplit && <FundingLegend />}
      <ul className="divide-y divide-ink-min/15">
        {ranked.map((c) => {
          const pm = getPartyMeta(c);
          const raised = c.contributions ?? 0;
          // Width is share-of-leader. A zero leader (nobody has reported)
          // must not divide — every bar is simply empty, which is the
          // truthful picture of a race where no money exists yet.
          const pct = leader > 0 ? Math.round((raised / leader) * 100) : 0;
          const incumbent =
            c.incumbentChallenge === "I"
              ? incumbencyLabel("I", redrawnSeat, c.incumbentRecord?.seat, resultsMode)
              : null;
          const cash = cashOnHandDisplay(c.cashOnHand);

          return (
            <li key={c.id} className="py-3">
              <div className="flex items-baseline justify-between gap-3">
                <p className="min-w-0 font-sans text-sm text-ink-hi">
                  <span className={`font-mono text-xs ${pm.color}`}>{pm.label}</span>
                  <span className="mx-1.5 text-ink-min">·</span>
                  <span className="break-words">{candidateName(c)}</span>
                  {incumbent && (
                    <span className="ml-2 inline-block whitespace-nowrap bg-ink-hi/90 px-1.5 py-0.5 font-mono text-[10px] text-surface-base">
                      {incumbent}
                    </span>
                  )}
                  {showUnconfirmed && !c.confirmed && (
                    <span className="ml-2 font-mono text-[10px] text-ink-min">UNCONFIRMED</span>
                  )}
                </p>
                <p className="shrink-0 font-mono text-xs tabular-nums text-ink-lo">
                  {raisedLabel(c)}
                </p>
              </div>

              <div
                className="mt-2 h-1.5 w-full bg-ink-min/15"
                role="img"
                aria-label={
                  leader > 0
                    ? raised > 0
                      ? `${formatCurrency(raised)} raised, ${pct}% of the leader in this race`
                      : raisedLabel(c)
                    : "no fundraising reported in this race"
                }
              >
                <div className={`h-1.5 ${pm.rule}`} style={{ width: `${pct}%` }} />
              </div>

              {cash && (
                <p className="mt-1 font-mono text-[11px] text-ink-min">
                  {cash.label.toLowerCase()} {cash.amount}
                  {c.candidateLoans
                    ? ` · lent own campaign ${formatCurrency(c.candidateLoans)}`
                    : ""}
                </p>
              )}

              {c.fundingSources && <FundingStrip sources={c.fundingSources} />}

              {c.fecFiled !== false && c.lastFinancialsSync && (
                <a
                  href={fecCandidateUrl(c.id, cycleYear)}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="mt-1.5 inline-block font-mono text-[11px] text-ink-lo hover:text-phos"
                >
                  {c.financialsThrough
                    ? `FEC figures through ${c.financialsThrough}`
                    : `FEC figures, checked ${c.lastFinancialsSync.slice(0, 10)}`}{" "}
                  ↗
                </a>
              )}

              {c.incumbentRecord && (
                <Link
                  href={`/politicians/${c.incumbentRecord.id}`}
                  className="mt-1.5 inline-block font-mono text-[11px] text-ink-lo hover:text-phos"
                >
                  representation score{" "}
                  <span className={getScoreColor(displayScore(c.incumbentRecord.score))}>
                    {displayScore(c.incumbentRecord.score)}
                  </span>{" "}
                  →
                </Link>
              )}
              {!c.incumbentRecord && c.memberRecord && (
                // Their record, not this seat's: named as what they are now.
                <Link
                  href={`/politicians/${c.memberRecord.id}`}
                  className="mt-1.5 inline-block font-mono text-[11px] text-ink-lo hover:text-phos"
                >
                  {memberRecordLabel(c.memberRecord).replace(/^./, (ch) => ch.toLowerCase())} ·
                  representation score{" "}
                  <span className={getScoreColor(displayScore(c.memberRecord.score))}>
                    {displayScore(c.memberRecord.score)}
                  </span>{" "}
                  →
                </Link>
              )}
            </li>
          );
        })}
      </ul>
    </>
  );
}
