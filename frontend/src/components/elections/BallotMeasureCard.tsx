import { measureStatusLabel } from "@/lib/elections";
import type { BallotMeasure } from "@/types/election";

/** One ballot measure.
 *
 * Everything rendered here is verbatim source text with its author named
 * beside it. There is deliberately no plain-language rewrite: the local
 * model cannot be checked for the failure that matters most on a ballot
 * — saying a YES vote does the opposite of what it does — because the
 * platform's grounding checks verify that tokens came from the source,
 * not that the claim points the same direction (see
 * docs/ballot-measures.md §6.4).
 */
export default function BallotMeasureCard({ measure }: { measure: BallotMeasure }) {
  const removed = measure.status === "removed" || measure.status === "withdrawn";

  return (
    <article
      className={`border p-4 ${
        removed
          ? "border-red-400/30 bg-red-950/10"
          : "border-matrix-green/20 bg-terminal-bg/50"
      }`}
    >
      <div className="flex items-start justify-between gap-3 flex-wrap mb-2">
        <h3 className="font-pixel text-sm text-white/90">
          {measure.number || measure.title}
        </h3>
        <span
          className={`text-[9px] font-pixel px-1.5 py-0.5 border ${
            removed
              ? "border-red-400/40 text-red-300/80"
              : "border-matrix-green/30 text-matrix-green/60"
          }`}
        >
          {measureStatusLabel(measure.status)}
        </span>
      </div>

      {removed && (
        // Shown, not hidden. Someone who saw this measure last week needs
        // to be told it was struck; a card that simply disappeared cannot
        // say that.
        <p className="text-xs text-red-300/80 mb-3">
          This measure is no longer on the ballot. It is kept here so a
          change since your last visit is visible rather than silent.
        </p>
      )}

      {measure.number && measure.title && (
        <p className="text-sm text-matrix-green/80 mb-3">{measure.title}</p>
      )}

      {measure.officialTitle && (
        <section className="mb-3">
          <h4 className="font-pixel text-[10px] text-matrix-green/50 mb-1">
            OFFICIAL BALLOT TITLE
          </h4>
          <blockquote className="text-xs text-matrix-green/70 border-l-2 border-matrix-green/20 pl-3">
            {measure.officialTitle}
          </blockquote>
          {measure.titleAuthority && (
            // Naming the drafter is MORE neutral than the bare quote:
            // ballot titles are routinely litigated as slanted, and who
            // wrote one is what tells a reader how to weigh it.
            <p className="text-[10px] text-matrix-green/40 mt-1">
              Drafted by {measure.titleAuthority}
            </p>
          )}
        </section>
      )}

      {measure.officialSummary && (
        <section className="mb-3">
          <h4 className="font-pixel text-[10px] text-matrix-green/50 mb-1">
            OFFICIAL SUMMARY
          </h4>
          <p className="text-xs text-matrix-green/70 whitespace-pre-line">
            {measure.officialSummary}
          </p>
        </section>
      )}

      {(measure.yesMeans || measure.noMeans) && (
        <section className="mb-3 grid gap-2 sm:grid-cols-2">
          {measure.yesMeans && (
            <div className="border border-matrix-green/15 p-2.5">
              <h4 className="font-pixel text-[10px] text-neon-cyan/60 mb-1">A YES VOTE</h4>
              <p className="text-xs text-matrix-green/70">{measure.yesMeans}</p>
            </div>
          )}
          {measure.noMeans && (
            <div className="border border-matrix-green/15 p-2.5">
              <h4 className="font-pixel text-[10px] text-neon-cyan/60 mb-1">A NO VOTE</h4>
              <p className="text-xs text-matrix-green/70">{measure.noMeans}</p>
            </div>
          )}
        </section>
      )}

      {measure.fiscalImpact && (
        <section className="mb-3">
          <h4 className="font-pixel text-[10px] text-matrix-green/50 mb-1">FISCAL IMPACT</h4>
          <p className="text-xs text-matrix-green/70 whitespace-pre-line">
            {measure.fiscalImpact}
          </p>
          {measure.fiscalAuthority && (
            <p className="text-[10px] text-matrix-green/40 mt-1">
              Prepared by {measure.fiscalAuthority}
            </p>
          )}
        </section>
      )}

      <footer className="flex items-center justify-between gap-3 flex-wrap pt-2 border-t border-matrix-green/10">
        <p className="text-[10px] text-matrix-green/40">
          Quoted verbatim from {measure.sourceName || "the source"}
          {measure.asOf ? ` · as of ${measure.asOf.slice(0, 10)}` : ""}
        </p>
        {measure.sourceUrl && (
          <a
            href={measure.sourceUrl}
            target="_blank"
            rel="noopener noreferrer"
            aria-label={`Read the full text of ${measure.number || measure.title} at the source (opens in new tab)`}
            className="text-[10px] font-pixel text-neon-cyan/70 hover:text-neon-cyan"
          >
            FULL TEXT AT SOURCE ↗
          </a>
        )}
      </footer>
    </article>
  );
}
