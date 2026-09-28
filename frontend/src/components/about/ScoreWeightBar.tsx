"use client";

import { useConfig } from "@/hooks/useConfig";
import { SCORE_TERMS, type ScoreKey } from "@/lib/scoreTerms";

/**
 * The Representation Score's three parts drawn to their real weights.
 *
 * Weights come from GET /api/config (config_definitions.SCORE_WEIGHTS), not
 * from this file — the frontend never keeps its own copy of a weight. Until
 * the config arrives the parts are drawn equal and unlabelled, which is
 * close enough to be honest and never shows a number the backend didn't say.
 */
const PARTS: readonly { key: ScoreKey; fill: string }[] = [
  { key: "fundingIndependence", fill: "bg-signal-cyan" },
  { key: "constituentAlignment", fill: "bg-signal-amber" },
  { key: "legislativeEffectiveness", fill: "bg-signal-magenta" },
];

export default function ScoreWeightBar() {
  const weights = useConfig()?.scoreWeights;
  const total = weights ? PARTS.reduce((s, p) => s + (weights[p.key] ?? 0), 0) : 0;
  const share = (key: ScoreKey) =>
    weights && total > 0 ? (weights[key] ?? 0) / total : 1 / PARTS.length;

  return (
    <figure aria-label="How the Representation Score is weighted">
      <div className="flex h-3 w-full gap-0.5" aria-hidden="true">
        {PARTS.map((p) => (
          <div key={p.key} className={p.fill} style={{ width: `${share(p.key) * 100}%` }} />
        ))}
      </div>
      <figcaption className="mt-3 grid gap-2 sm:grid-cols-3">
        {PARTS.map((p) => (
          <span key={p.key} className="flex items-baseline gap-2 text-sm text-ink">
            <span aria-hidden="true" className={`h-2.5 w-2.5 shrink-0 ${p.fill}`} />
            <span>
              {SCORE_TERMS[p.key].label}
              {weights && total > 0 && (
                <span className="ml-1.5 font-mono text-ink-lo">
                  {Math.round(share(p.key) * 100)}%
                </span>
              )}
            </span>
          </span>
        ))}
      </figcaption>
    </figure>
  );
}
