import type { Court } from "@/types/court";
import { splitLabel, termsPhrase } from "@/lib/court";

// Lopsided splits light, close ones dark, in the order the API gives
// them; a split past the end takes the darkest. Text on the first three is
// the page's darkest ink, on the rest its heading ink: both clear 4.5:1 on
// their shade (court.test.ts).
export const SPLIT_SHADES = ["#F2EEE7", "#CDC7BC", "#A39D93", "#5E5A54", "#45423D", "#33302C"];
export const DARK_TEXT_SHADES = 3;
const DARK_TEXT = "#0E0C0A";
const LIGHT_TEXT = "#F2EEE7";
// A segment narrower than this leaves its label to the table.
const MIN_LABEL_SHARE = 0.12;

export function shade(i: number): { background: string; color: string } {
  const k = Math.min(i, SPLIT_SHADES.length - 1);
  return { background: SPLIT_SHADES[k], color: k < DARK_TEXT_SHADES ? DARK_TEXT : LIGHT_TEXT };
}

/**
 * How the Court split, term by term: one bar per term, each segment a
 * split (9–0 … 5–4) sized by its share of the term's cases. The bars are
 * drawn; the table under them is the same counts in rows.
 */
export default function DecisionLineups({ court }: { court: Court }) {
  const { splits, terms } = court.lineups;
  if (terms.length === 0) return null;
  return (
    <div className="flex flex-col gap-4">
      <h3 className="text-lg font-bold text-ink-hi">How the Court split, by term</h3>
      <div aria-hidden="true" className="flex flex-col gap-2">
        {terms.map((t) => (
          <div key={t.term} className="flex items-center gap-3">
            <span className="w-12 shrink-0 font-mono text-sm text-ink-lo">{t.term}</span>
            <div className="flex h-7 flex-1 gap-0.5">
              {splits.map((s, i) => {
                const count = t.counts[s] ?? 0;
                if (!count) return null;
                const share = count / t.cases;
                return (
                  <span
                    key={s}
                    className="@container flex items-center justify-center overflow-hidden font-mono text-xs"
                    style={{ ...shade(i), width: `${share * 100}%` }}
                  >
                    {/* Shown only where the segment is wide enough for it, a
                        container query in rem, so it follows the text size:
                        on a phone, and at 200% text, labels were cut off
                        mid-figure ("7–2 · 1"). The table below has them. */}
                    {share >= MIN_LABEL_SHARE && (
                      <span className="hidden whitespace-nowrap @min-[5.5rem]:inline">
                        {`${splitLabel(s)} · ${count}`}
                      </span>
                    )}
                  </span>
                );
              })}
            </div>
          </div>
        ))}
        <div className="flex flex-wrap gap-x-4 gap-y-1 pl-[3.75rem] font-mono text-xs text-ink-lo">
          {splits.map((s, i) => (
            <span key={s} className="inline-flex items-center gap-1.5">
              <span className="h-3 w-3" style={{ background: shade(i).background }} />
              {splitLabel(s)}
            </span>
          ))}
        </div>
      </div>

      <div className="overflow-x-auto" tabIndex={0} role="region" aria-label="Splits by term">
        <table className="w-full border-collapse text-sm">
          <caption className="mb-1 text-left text-sm text-ink-lo">
            Orally argued cases decided in {termsPhrase(terms.map((t) => t.term))}, by the
            Court&apos;s vote (majority&ndash;dissent). A count of 8 or fewer justices is a case a
            justice sat out.
          </caption>
          <thead>
            <tr className="border-b border-white/15 font-mono text-xs uppercase text-ink-min">
              <th scope="col" className="py-1.5 pr-3 text-left font-normal">
                Term
              </th>
              {splits.map((s) => (
                <th key={s} scope="col" className="px-2 py-1.5 text-right font-normal">
                  {splitLabel(s)}
                </th>
              ))}
              <th scope="col" className="py-1.5 pl-2 text-right font-normal">
                Cases
              </th>
            </tr>
          </thead>
          <tbody>
            {terms.map((t) => (
              <tr key={t.term} className="border-b border-white/[0.07] font-mono tabular-nums">
                <th scope="row" className="py-1.5 pr-3 text-left font-normal text-ink">
                  {t.term}
                </th>
                {splits.map((s) => (
                  <td key={s} className="px-2 py-1.5 text-right text-ink">
                    {t.counts[s] ?? 0}
                  </td>
                ))}
                <td className="py-1.5 pl-2 text-right text-ink-hi">{t.cases}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
