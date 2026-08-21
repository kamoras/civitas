import { isActiveCandidate } from "@/lib/elections";
import CandidateCard from "./CandidateCard";
import RaceFinancials from "./RaceFinancials";
import CoverageFeed from "./CoverageFeed";
import type { RaceWithCandidates, RaceCoverageItem } from "@/types/election";

/** Everything about one race, inline — candidates, fundraising, and its
 * own coverage — so a voter never has to leave the state ballot page for
 * "the full race detail" (2026-08 revamp: that used to live one click
 * away at /elections/[raceId], which was one nested page too many). */
export default function RaceFullDetail({
  race,
  coverage,
}: {
  race: RaceWithCandidates;
  coverage: RaceCoverageItem[];
}) {
  // FEC candidate files include paper filers and prior-cycle records —
  // collapse those under "Other FEC filers" so the page stays honest
  // without deleting anyone (same rule race-detail used to apply).
  const active = race.candidates.filter(isActiveCandidate);
  const otherFilers = race.candidates.filter((c) => !isActiveCandidate(c));

  return (
    <div id={`race-${race.id}`} className="scroll-mt-24">
      {active.length === 0 ? (
        <p className="text-xs text-ink-lo">No candidates on record for this race yet.</p>
      ) : (
        <div className="space-y-3">
          {active.map((c) => (
            <CandidateCard key={c.id} candidate={c} />
          ))}
        </div>
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

      {active.length > 0 && (
        <div className="mt-4">
          <RaceFinancials candidates={active} />
          <p className="mt-3 font-mono text-xs leading-relaxed text-ink-min">
            Per FEC filings — totals lag by up to a quarter and amendments. Source: fec.gov.
          </p>
        </div>
      )}

      {coverage.length > 0 && (
        <div className="mt-4">
          <h3 className="mb-2 font-mono text-xs uppercase tracking-[0.14em] text-ink-min">
            Coverage of this race ({coverage.length})
          </h3>
          <CoverageFeed items={coverage} />
        </div>
      )}
    </div>
  );
}
