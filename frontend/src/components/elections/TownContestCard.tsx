import { safeHref } from "@/lib/formatting";
import type { TownBallotItem } from "@/types/election";

/** One local contest or measure from a town-level lookup.
 *
 * Same verbatim-only contract as BallotMeasureCard: nothing here is
 * model-generated (AGENTS.md principle 7). Candidate names/parties and
 * measure text come straight from Google Civic's response for the town's
 * representative address — not a visitor's own address, see
 * GOOGLE_CIVIC_API_KEY's comment in config.py.
 */
export default function TownContestCard({ item }: { item: TownBallotItem }) {
  if (item.kind === "contest") {
    return (
      <article className="border border-matrix-green/20 bg-terminal-bg/50 p-4">
        <h3 className="font-pixel text-sm text-white/90 mb-2">{item.office}</h3>
        {item.candidates.length > 0 ? (
          <ul className="space-y-1">
            {item.candidates.map((c, i) => {
              const href = safeHref(c.candidateUrl);
              return (
                <li key={i} className="text-xs text-matrix-green/80 flex items-center gap-2">
                  <span>{c.name}</span>
                  {c.party && <span className="text-matrix-green/40">({c.party})</span>}
                  {href && (
                    <a
                      href={href}
                      target="_blank"
                      rel="noopener noreferrer"
                      aria-label={`${c.name}'s campaign site (opens in new tab)`}
                      className="text-neon-cyan/70 hover:text-neon-cyan text-[10px]"
                    >
                      SITE ↗
                    </a>
                  )}
                </li>
              );
            })}
          </ul>
        ) : (
          <p className="text-xs text-matrix-green/40">No candidates listed.</p>
        )}
      </article>
    );
  }

  const measureHref = safeHref(item.url);
  return (
    <article className="border border-matrix-green/20 bg-terminal-bg/50 p-4">
      <h3 className="font-pixel text-sm text-white/90 mb-2">{item.title}</h3>
      {item.subtitle && <p className="text-sm text-matrix-green/80 mb-3">{item.subtitle}</p>}
      {item.text && (
        <p className="text-xs text-matrix-green/70 whitespace-pre-line mb-3">{item.text}</p>
      )}
      {item.passageThreshold && (
        <p className="text-[10px] text-matrix-green/40 mb-2">
          Passage threshold: {item.passageThreshold}
        </p>
      )}
      {measureHref && (
        <a
          href={measureHref}
          target="_blank"
          rel="noopener noreferrer"
          aria-label={`Read the full text of ${item.title} at the source (opens in new tab)`}
          className="text-[10px] font-pixel text-neon-cyan/70 hover:text-neon-cyan"
        >
          FULL TEXT AT SOURCE ↗
        </a>
      )}
    </article>
  );
}
