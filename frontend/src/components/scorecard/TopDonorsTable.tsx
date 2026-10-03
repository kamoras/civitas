import type { Donor } from "@/types/senator";
import { formatCurrency } from "@/lib/formatting";
import { fecCommitteeSearchUrl } from "@/lib/sources";

// PAC analyses that restate the donation rather than saying anything about
// the PAC — shown nowhere.
const EMPTY_ANALYSIS =
  /has received funding from|a political PAC|opposes the removal|which is (?:not )?(?:aligned with|related to) (?:his|her|their) (?:platform|stance|stated)/i;
const NO_SPONSOR = new Set(["unclear", "unknown", "n/a", "none", ""]);

/** The member's top ten donors with who is behind each PAC, as the
 *  scorecard's drawer lists them. Moved unchanged from the old card. */
export default function TopDonorsTable({ donors }: { donors: Donor[] }) {
  if (donors.length === 0) return null;
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <caption className="mb-2 text-left font-mono text-xs uppercase tracking-[0.14em] text-ink-min">
          Top donors · fec.gov
        </caption>
        <thead>
          <tr className="border-b border-white/[0.07] text-xs text-ink-min">
            <th scope="col" className="py-2 pr-2 sm:pr-4 text-left">
              RANK
            </th>
            <th scope="col" className="py-2 pr-2 sm:pr-4 text-left">
              DONOR
            </th>
            <th scope="col" className="py-2 pr-2 sm:pr-4 text-right">
              AMOUNT
            </th>
            <th scope="col" className="py-2 text-right">
              TYPE
            </th>
          </tr>
        </thead>
        <tbody>
          {donors.slice(0, 10).map((donor, i) => (
            <tr
              key={donor.name}
              className={`border-b border-white/[0.07] ${i % 2 === 0 ? "bg-white/[0.03]" : ""}`}
            >
              <td className="py-2 pr-2 sm:pr-4 text-ink-min">#{i + 1}</td>
              <td className="py-2 pr-2 sm:pr-4">
                {/* An FEC employer name can be one unspaced token: wrap it
                    rather than push AMOUNT and TYPE off a phone's screen. */}
                <div className="text-ink [overflow-wrap:anywhere]">
                  {donor.type === "PAC" || donor.type === "SuperPAC" ? (
                    <a
                      href={fecCommitteeSearchUrl(donor.name)}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="underline decoration-white/15 underline-offset-2 transition-colors hover:text-phos hover:decoration-signal-cyan"
                    >
                      {donor.name}
                    </a>
                  ) : (
                    donor.name
                  )}
                </div>
                {donor.pacSponsor &&
                  donor.pacSponsor.toLowerCase() !== donor.name.toLowerCase() &&
                  !NO_SPONSOR.has(donor.pacSponsor.toLowerCase().trim()) &&
                  donor.pacSponsor.length > 2 && (
                    <div className="mt-0.5 text-xs text-ink-lo [overflow-wrap:anywhere]">
                      BEHIND THE PAC: {donor.pacSponsor}
                    </div>
                  )}
                {donor.pacAnalysis && !EMPTY_ANALYSIS.test(donor.pacAnalysis) && (
                  <div className="mt-0.5 text-xs text-ink-min [overflow-wrap:anywhere]">
                    {donor.pacAnalysis}
                  </div>
                )}
              </td>
              <td className="py-2 pr-2 sm:pr-4 text-right text-signal-cyan">
                {formatCurrency(donor.total)}
              </td>
              <td className="py-2 text-right">
                <div className="text-xs text-ink-min">
                  {donor.type === "CandidateAffiliated"
                    ? "Own Committee"
                    : donor.type === "Self-Funded"
                      ? "Self-Funded"
                      : donor.type}
                </div>
                {donor.pacIndustry && donor.pacIndustry !== "OTHER" && (
                  <div className="mt-0.5 text-xs text-ink-lo">
                    {donor.pacIndustry.replace("_", " ")}
                  </div>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
