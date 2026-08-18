import type { CandidateSummary } from "@/types/election";
import { formatCurrency } from "@/lib/formatting";

// FEC's party codes ("DEM"/"REP"/"IND"/...) don't match lib/partyStyles.ts's
// D/R/I keys (those back President/Justice's own party codes), so this
// mirrors PresidentClient.tsx's local PARTY_META + getPartyMeta fallback
// pattern rather than reusing partyStyles.ts directly.
const PARTY_META: Record<string, { label: string; color: string; rule: string }> = {
  DEM: { label: "DEMOCRAT", color: "text-dem-blue", rule: "bg-dem-blue" },
  REP: { label: "REPUBLICAN", color: "text-signal-red", rule: "bg-signal-red" },
  IND: { label: "INDEPENDENT", color: "text-ind-purple", rule: "bg-ind-purple" },
  // State affiliates of the Democratic Party — styled as Democrats.
  DFL: { label: "DEMOCRAT (DFL)", color: "text-dem-blue", rule: "bg-dem-blue" },
  DNL: { label: "DEMOCRAT (D-NPL)", color: "text-dem-blue", rule: "bg-dem-blue" },
  LIB: { label: "LIBERTARIAN", color: "text-ink-lo", rule: "bg-ink-min" },
  GRE: { label: "GREEN", color: "text-phos-mid", rule: "bg-phos-mid" },
  CON: { label: "CONSTITUTION", color: "text-ink-lo", rule: "bg-ink-min" },
  NON: { label: "NO PARTY AFFILIATION", color: "text-ink-lo", rule: "bg-ink-min" },
  NPA: { label: "NO PARTY AFFILIATION", color: "text-ink-lo", rule: "bg-ink-min" },
  NNE: { label: "NO PARTY AFFILIATION", color: "text-ink-lo", rule: "bg-ink-min" },
  UNK: { label: "UNAFFILIATED/UNKNOWN", color: "text-ink-lo", rule: "bg-ink-min" },
};

function getPartyMeta(party: string) {
  return PARTY_META[party] ?? { label: party, color: "text-ink-lo", rule: "bg-ink-min" };
}

const INCUMBENT_LABELS: Record<string, string> = {
  I: "INCUMBENT",
  C: "CHALLENGER",
  O: "OPEN SEAT",
};

export default function CandidateCard({ candidate }: { candidate: CandidateSummary }) {
  const pm = getPartyMeta(candidate.party);
  // UTC date only, sliced from the ISO string — deterministic across
  // server and client renders, so no locale/hydration hazard.
  const syncedOn = candidate.lastFinancialsSync?.slice(0, 10) ?? null;

  return (
    // Party reads as a 3px rule down the left edge rather than a tinted
    // outline around the whole card: it identifies the candidate without
    // wrapping every figure in a partisan colour.
    <article className="relative border border-white/[0.09] bg-surface p-4 pl-5">
      <span className={`absolute inset-y-0 left-0 w-[3px] ${pm.rule}`} aria-hidden="true" />

      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h4 className="font-display text-lg font-semibold leading-tight text-ink-hi">
            <a
              href={`https://www.fec.gov/data/candidate/${encodeURIComponent(candidate.id)}/`}
              target="_blank"
              rel="noopener noreferrer"
              className="transition-colors hover:text-phos"
            >
              {candidate.name}{" "}
              <span aria-hidden="true" className="font-mono text-xs text-phos-mid">
                ↗
              </span>
            </a>
          </h4>
          <p className={`mt-0.5 font-mono text-xs tracking-[0.1em] ${pm.color}`}>{pm.label}</p>
        </div>

        {candidate.incumbentChallenge && (
          <span className="shrink-0 border border-white/15 px-2 py-0.5 font-mono text-xs tracking-[0.1em] text-ink-lo">
            {INCUMBENT_LABELS[candidate.incumbentChallenge] ?? candidate.incumbentChallenge}
          </span>
        )}
      </div>

      {syncedOn == null ? (
        // Never synced ≠ raised $0 — don't show figures that read as zeros.
        <p className="mt-3 font-mono text-xs tracking-[0.12em] text-ink-min">AWAITING FEC SYNC</p>
      ) : (
        <>
          <dl className="mt-3 grid grid-cols-2 gap-x-8 gap-y-1">
            <div>
              <dt className="font-mono text-xs uppercase tracking-[0.12em] text-ink-min">Raised</dt>
              <dd className="font-mono text-xl tabular-nums text-ink-hi">
                {candidate.contributions != null ? formatCurrency(candidate.contributions) : "—"}
              </dd>
            </div>
            <div>
              <dt className="font-mono text-xs uppercase tracking-[0.12em] text-ink-min">
                Cash on hand
              </dt>
              <dd className="font-mono text-xl tabular-nums text-ink-hi">
                {candidate.cashOnHand != null ? formatCurrency(candidate.cashOnHand) : "—"}
              </dd>
            </div>
          </dl>
          <p className="mt-2 font-mono text-xs tracking-[0.08em] text-ink-min">AS OF {syncedOn}</p>
        </>
      )}
    </article>
  );
}
