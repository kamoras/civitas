import type { Senator } from "@/types/senator";
import type { RepresentationScoreBreakdown } from "@/types/scoreBreakdown";
import { displayScore } from "@/lib/formatting";
import { getScoreColor } from "@/lib/representation";

/** The three scored parts, in the columns' order: the short word a reader
 *  scanning the list knows them by, the breakdown's key and the column's
 *  anchor (ScoreColumn's shareId). */
const PARTS = [
  { word: "Money", key: "fundingIndependence", anchor: "funding-independence" },
  { word: "Votes", key: "constituentAlignment", anchor: "constituent-alignment" },
  { word: "Bills", key: "legislativeEffectiveness", anchor: "legislative-effectiveness" },
] as const;

/**
 * The scorecard in three lines, for the top of a phone's first screen: each
 * scored part's one-sentence finding (the breakdown API's, a template around
 * the scorer's own figures) under a plain word for what it is about, with its
 * score, linking down to the column that shows the evidence. On a desktop the
 * columns themselves are on the first screen, so this is left out there.
 */
export default function AtAGlance({
  breakdown,
  scores,
}: {
  breakdown: RepresentationScoreBreakdown | null;
  scores: Senator["representationScore"];
}) {
  const rows = PARTS.map((p) => ({ ...p, finding: breakdown?.[p.key]?.finding })).filter(
    (p) => p.finding
  );
  if (!rows.length) return null;
  return (
    <ul className="flex flex-col lg:hidden" aria-label="The scorecard in short">
      {rows.map((p) => {
        const value = scores[p.key];
        const shown = value == null ? null : displayScore(value);
        return (
          <li key={p.key} className="border-t border-white/[0.07]">
            <a href={`#${p.anchor}`} className="group block py-1.5 text-sm leading-snug text-ink">
              {/* A run-in label, so a row is the sentence's own lines. */}
              <span className="mr-2 font-mono text-xs uppercase tracking-[0.14em] text-ink-min">
                {p.word}
                {shown != null && (
                  <>
                    {" "}
                    <span className={`tabular-nums ${getScoreColor(shown)}`}>{shown}</span>
                  </>
                )}
              </span>{" "}
              <span className="group-hover:underline">{p.finding}</span>
            </a>
          </li>
        );
      })}
    </ul>
  );
}
