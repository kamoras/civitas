import type { CandidateSummary } from "@/types/election";
import { formatCurrency } from "@/lib/formatting";

// FEC's party codes ("DEM"/"REP"/"IND"/...) don't match lib/partyStyles.ts's
// D/R/I keys (those back President/Justice's own party codes), so this
// mirrors PresidentClient.tsx's local PARTY_META + getPartyMeta fallback
// pattern rather than reusing partyStyles.ts directly.
const PARTY_META: Record<string, { label: string; color: string; border: string }> = {
  DEM: { label: "DEMOCRAT", color: "text-dem-blue", border: "border-dem-blue/40" },
  REP: { label: "REPUBLICAN", color: "text-rep-red", border: "border-rep-red/40" },
  IND: { label: "INDEPENDENT", color: "text-white/70", border: "border-white/30" },
  // State affiliates of the Democratic Party — styled as Democrats.
  DFL: { label: "DEMOCRAT (DFL)", color: "text-dem-blue", border: "border-dem-blue/40" },
  DNL: { label: "DEMOCRAT (D-NPL)", color: "text-dem-blue", border: "border-dem-blue/40" },
  LIB: { label: "LIBERTARIAN", color: "text-white/70", border: "border-white/30" },
  GRE: { label: "GREEN", color: "text-matrix-green/80", border: "border-matrix-green/40" },
  CON: { label: "CONSTITUTION", color: "text-white/70", border: "border-white/30" },
  NON: { label: "NO PARTY AFFILIATION", color: "text-white/50", border: "border-white/20" },
  NPA: { label: "NO PARTY AFFILIATION", color: "text-white/50", border: "border-white/20" },
  NNE: { label: "NO PARTY AFFILIATION", color: "text-white/50", border: "border-white/20" },
  UNK: { label: "UNAFFILIATED/UNKNOWN", color: "text-white/50", border: "border-white/20" },
};

function getPartyMeta(party: string) {
  return PARTY_META[party] ?? { label: party, color: "text-white/50", border: "border-white/20" };
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
    <div className={`border ${pm.border} bg-terminal-bg/50 p-4`}>
      <div className="flex items-start justify-between gap-2 mb-2">
        <h4 className="font-pixel text-sm text-white/90">
          <a
            href={`https://www.fec.gov/data/candidate/${encodeURIComponent(candidate.id)}/`}
            target="_blank"
            rel="noopener noreferrer"
            className="hover:text-neon-cyan transition-colors"
          >
            {candidate.name}{" "}
            <span aria-hidden="true" className="text-[10px] text-neon-cyan/60">
              ↗
            </span>
          </a>
        </h4>
        {candidate.incumbentChallenge && (
          <span className="text-[9px] font-pixel px-1.5 py-0.5 border border-matrix-green/20 text-matrix-green/50 shrink-0">
            {INCUMBENT_LABELS[candidate.incumbentChallenge] ?? candidate.incumbentChallenge}
          </span>
        )}
      </div>
      <span className={`text-[10px] font-pixel ${pm.color}`}>{pm.label}</span>

      {syncedOn == null ? (
        // Never synced ≠ raised $0 — don't show figures that read as zeros.
        <p className="mt-3 text-[9px] font-pixel text-matrix-green/40 tracking-widest">
          AWAITING FEC SYNC
        </p>
      ) : (
        <>
          <div className="grid grid-cols-2 gap-2 mt-3">
            <div className="text-center border border-matrix-green/20 px-2 py-1.5">
              <div className="text-[9px] text-matrix-green/40 tracking-widest">RAISED</div>
              <div className="text-sm font-bold text-white/80 tabular-nums">
                {candidate.contributions != null ? formatCurrency(candidate.contributions) : "—"}
              </div>
            </div>
            <div className="text-center border border-matrix-green/20 px-2 py-1.5">
              <div className="text-[9px] text-matrix-green/40 tracking-widest">CASH ON HAND</div>
              <div className="text-sm font-bold text-white/80 tabular-nums">
                {candidate.cashOnHand != null ? formatCurrency(candidate.cashOnHand) : "—"}
              </div>
            </div>
          </div>
          <p className="mt-1.5 text-[8px] font-pixel text-matrix-green/30 tracking-widest">
            AS OF {syncedOn}
          </p>
        </>
      )}
    </div>
  );
}
