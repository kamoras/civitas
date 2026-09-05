import { useState } from "react";
import { isActiveCandidate, raceBadgeLabel, tierCandidates } from "@/lib/elections";
import { formatCurrency } from "@/lib/formatting";
import CandidateCard from "./CandidateCard";
import RaceFinancials from "./RaceFinancials";
import type { BallotCandidate, RaceWithCandidates } from "@/types/election";

const PARTY_SWATCH: Record<string, string> = {
  DEM: "bg-dem-blue", REP: "bg-rep-red", IND: "bg-ind-purple",
  DFL: "bg-dem-blue", DNL: "bg-dem-blue",
};

/** What this race's candidate list actually IS. Four quite different
 * things get shown in the same shape — nominees a state has confirmed,
 * people merely on a primary ballot, nominees inferred from primary
 * results (which can't see a general-only Libertarian/Green/independent),
 * and raw FEC filers who may never appear on any ballot — and a reader
 * can't tell them apart without being told. The backend decides which
 * (RaceWithCandidates.candidateSource); this only puts it into words. */
const SOURCE_NOTE: Record<RaceWithCandidates["candidateSource"], string> = {
  confirmed: "This state's official general-election ballot for this race.",
  nominees:
    "Nominees confirmed by this state's primary results. Candidates who reach the general election without running in a primary — Libertarian, Green or independent — aren't covered for this state yet, so this list may be short.",
  primary: "Ranked by cash on hand — the nominee isn't decided until this state's primary.",
  filers: "Ranked by cash on hand — this state's nominees aren't confirmed yet, so this is every FEC filer.",
};

/** A leader gets the fuller CandidateCard; anyone in the tail gets one
 * compact line — same party-colour dot the leader cards carry on their
 * left edge, name, and amount, nothing else. This is what an "everyone
 * who filed" race actually looks like laid flat: a couple of real
 * contenders and a long, real, much smaller tail — not a warning, a
 * shape. */
function TailRow({ candidate }: { candidate: BallotCandidate }) {
  const debt = candidate.cashOnHand != null && candidate.cashOnHand < 0;
  return (
    <div className="flex items-center gap-2.5 border-b border-white/[0.05] py-1.5 text-sm last:border-b-0">
      <span
        className={`h-2 w-2 shrink-0 ${PARTY_SWATCH[candidate.party] ?? "bg-ink-min"}`}
        aria-hidden="true"
      />
      <span className="flex-1 truncate text-ink-lo">{candidate.name}</span>
      <span className="shrink-0 font-mono text-xs text-ink-min">
        {candidate.cashOnHand == null
          ? "—"
          : debt
            ? `debt ${formatCurrency(Math.abs(candidate.cashOnHand))}`
            : formatCurrency(candidate.cashOnHand)}
      </span>
    </div>
  );
}

/** Everything about one race, inline — candidates and fundraising — so a
 * voter never has to leave the state ballot page for "the full race
 * detail" (2026-08 revamp: that used to live one click away at
 * /elections/[raceId], which was one nested page too many).
 *
 * Coverage is NOT repeated here: it used to render its own per-race
 * CoverageFeed, which meant every Senate race's articles (that section
 * always rendered) and the selected House race's articles appeared
 * twice on the page — once here, once in the page-level feed (2026-08
 * report: "the same articles show up twice on the state page"). The
 * single top-level feed already tags each item with its race badge
 * (CoverageFeed.tsx), so one list serves both "browse everything" and
 * "what's about this race" without duplicating a single article.
 *
 * Only an unconfirmed race ("filers"/"primary") gets tiered into
 * leaders + a collapsible tail (see lib/elections.ts's tierCandidates) —
 * a "confirmed"/"nominees" race is already a real, small, state-verified
 * list, and tiering an already-trustworthy list would just be
 * complexity with nothing to say (2026-09 fix: TX-quality races showed
 * one flat list of full cards before this and still do; only the noisy
 * races changed shape). */
export default function RaceFullDetail({ race }: { race: RaceWithCandidates }) {
  const [tailOpen, setTailOpen] = useState(false);
  // FEC candidate files include paper filers and prior-cycle records —
  // collapse those under "Other FEC filers" so the page stays honest
  // without deleting anyone (same rule race-detail used to apply).
  const active = race.candidates.filter(isActiveCandidate);
  const otherFilers = race.candidates.filter((c) => !isActiveCandidate(c));
  const tiered = race.candidateSource === "filers" || race.candidateSource === "primary";
  const { leaders, tail } = tiered ? tierCandidates(active) : { leaders: active, tail: [] };

  return (
    <div id={`race-${race.id}`} className="scroll-mt-24">
      {active.length === 0 ? (
        <p className="text-xs text-ink-lo">No candidates on record for this race yet.</p>
      ) : (
        <>
          <div className="mb-2 flex items-center gap-2">
            <p className="font-mono text-xs text-ink-min">{SOURCE_NOTE[race.candidateSource]}</p>
            {tiered && (
              <span className="shrink-0 border border-white/15 px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-[0.1em] text-ink-min">
                FEC-filed field
              </span>
            )}
          </div>
          <div className="space-y-3">
            {leaders.map((c) => (
              <CandidateCard key={c.id} candidate={c} />
            ))}
          </div>

          {tail.length > 0 && (
            <>
              <button
                type="button"
                onClick={() => setTailOpen((v) => !v)}
                aria-expanded={tailOpen}
                className="mt-3 flex w-full items-center gap-2 border border-dashed border-white/15 px-3 py-2 font-mono text-xs text-ink-lo transition-colors hover:border-white/30 hover:text-ink-hi"
              >
                <span className={`inline-block transition-transform ${tailOpen ? "rotate-90" : ""}`}>
                  ▸
                </span>
                {tail.length} more filed
              </button>
              {tailOpen && (
                <div className="mt-1 border border-white/[0.09] bg-surface px-3 py-1">
                  {tail.map((c) => (
                    <TailRow key={c.id} candidate={c} />
                  ))}
                </div>
              )}
            </>
          )}
        </>
      )}

      {otherFilers.length > 0 && (
        <details className="mt-3 border border-white/[0.09] bg-surface p-4">
          <summary className="cursor-pointer font-mono text-xs uppercase tracking-[0.14em] text-ink-lo hover:text-ink-hi">
            Other FEC filers ({otherFilers.length})
          </summary>
          <p className="mt-2 font-mono text-xs leading-relaxed text-ink-min">
            Paper filers and prior-cycle candidates on FEC record who have not raised funds this
            cycle.
          </p>
          <div className="mt-3 space-y-3">
            {otherFilers.map((c) => (
              <CandidateCard key={c.id} candidate={c} />
            ))}
          </div>
        </details>
      )}

      {leaders.length > 0 && (
        <div className="mt-4">
          <RaceFinancials candidates={leaders} />
          <p className="mt-3 font-mono text-xs leading-relaxed text-ink-min">
            Per FEC filings — totals lag by up to a quarter and amendments. Source: fec.gov.
          </p>
        </div>
      )}

      <p className="mt-4 font-mono text-xs text-ink-min">
        News coverage of this race is tagged{" "}
        <span className="border border-white/15 px-1.5 py-0.5 tracking-[0.1em] text-ink-lo">
          {raceBadgeLabel(race)}
        </span>{" "}
        in the News Coverage section above.
      </p>
    </div>
  );
}
