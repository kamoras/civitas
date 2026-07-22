"use client";

import { useState } from "react";
import {
  fetchJusticeScoreBreakdown,
  fetchPresidentScoreBreakdown,
  fetchRepScoreBreakdown,
  fetchSenatorScoreBreakdown,
} from "@/lib/api";
import { getScoreColor } from "@/lib/representation";
import type { ScoreBreakdownComponent } from "@/types/scoreBreakdown";
import Modal from "./Modal";

export type BreakdownEntityType = "senator" | "representative" | "president" | "justice";

interface ScoreBreakdownPanelProps {
  entityType: BreakdownEntityType;
  entityId: string;
  /** Key into the fetched breakdown dict, e.g. "fundingIndependence", "agencyAlignment", "consistency". */
  dimensionKey: string;
  label: string;
}

// Loosely typed on purpose: senator/rep/president dimensions share
// {score, components, note?} but president also has {score, seedOnly},
// and justice's breakdown sub-objects are a free-form {detail, ...numbers}
// bag (analyze_justice_votes' math doesn't decompose into weighted
// components the way the other three do) — forcing one rigid shape onto
// all four would fight the data more than it would help render it.
interface FetchedDimension {
  score?: number;
  components?: ScoreBreakdownComponent[];
  note?: string;
  seedOnly?: boolean;
  detail?: string;
  [key: string]: unknown;
}

type LoadState = "idle" | "loading" | "error" | "ready";

// Module-level cache of in-flight/completed fetches, keyed by entityType+id
// — every dimension's panel on a page (up to 6 for a president) shares one
// underlying request instead of each firing its own.
const _breakdownCache = new Map<string, Promise<Record<string, FetchedDimension>>>();

function fetchEntityBreakdown(
  entityType: BreakdownEntityType,
  entityId: string,
): Promise<Record<string, FetchedDimension>> {
  const key = `${entityType}:${entityId}`;
  const cached = _breakdownCache.get(key);
  if (cached) return cached;

  const promise = (async () => {
    if (entityType === "senator") {
      return (await fetchSenatorScoreBreakdown(entityId)) as unknown as Record<string, FetchedDimension>;
    }
    if (entityType === "representative") {
      return (await fetchRepScoreBreakdown(entityId)) as unknown as Record<string, FetchedDimension>;
    }
    if (entityType === "president") {
      return (await fetchPresidentScoreBreakdown(entityId)) as unknown as Record<string, FetchedDimension>;
    }
    const justice = await fetchJusticeScoreBreakdown(entityId);
    return justice.breakdown as unknown as Record<string, FetchedDimension>;
  })();
  _breakdownCache.set(key, promise);
  return promise;
}

function formatWeight(w: number): string {
  return `${Math.round(w * 100)}%`;
}

function ComponentRow({ c }: { c: ScoreBreakdownComponent }) {
  return (
    <div className="py-2.5 border-t border-matrix-green/10 first:border-t-0 first:pt-0">
      <div className="flex items-baseline justify-between gap-3">
        <span className="text-matrix-green/80 text-sm">
          {c.label}
          {c.weight !== undefined && (
            <span className="text-matrix-green/40"> ({formatWeight(c.weight)} of this score)</span>
          )}
        </span>
        {c.score !== undefined && (
          <span className={`font-mono shrink-0 text-base ${getScoreColor(c.score)}`}>{c.score.toFixed(1)}</span>
        )}
      </div>
      <div className="text-matrix-green/50 text-sm leading-relaxed mt-1">{c.detail}</div>
    </div>
  );
}

function DimensionBody({ dimension }: { dimension: FetchedDimension }) {
  if (dimension.seedOnly) {
    return (
      <p className="text-matrix-green/50 italic">
        Editorial estimate — not computed from a live formula. See the methodology page for sourcing.
      </p>
    );
  }
  if (dimension.components && dimension.components.length > 0) {
    return (
      <div>
        {dimension.components.map((c, i) => (
          <ComponentRow key={i} c={c} />
        ))}
        {dimension.note && (
          <p className="text-matrix-green/40 italic mt-3 pt-3 border-t border-matrix-green/10">{dimension.note}</p>
        )}
      </div>
    );
  }
  if (dimension.note) {
    return <p className="text-matrix-green/50 italic">{dimension.note}</p>;
  }
  // Justice dimensions: no components array, just a plain-language detail
  // string plus raw supporting numbers (already shown elsewhere on the page).
  if (dimension.detail) {
    return <p className="text-matrix-green/70 leading-relaxed">{dimension.detail}</p>;
  }
  return <p className="text-matrix-green/40 italic">No breakdown available.</p>;
}

export default function ScoreBreakdownPanel({ entityType, entityId, dimensionKey, label }: ScoreBreakdownPanelProps) {
  const [open, setOpen] = useState(false);
  const [state, setState] = useState<LoadState>("idle");
  const [dimension, setDimension] = useState<FetchedDimension | null>(null);

  const handleOpen = () => {
    setOpen(true);
    if (state === "idle") {
      setState("loading");
      fetchEntityBreakdown(entityType, entityId)
        .then((data) => {
          setDimension(data[dimensionKey] ?? null);
          setState("ready");
        })
        .catch(() => setState("error"));
    }
  };

  return (
    <div className="mt-1">
      <button
        type="button"
        onClick={handleOpen}
        className="text-[10px] text-matrix-green/40 hover:text-matrix-green/70 transition-colors underline underline-offset-2 cursor-pointer"
      >
        show the math
      </button>
      <Modal open={open} onClose={() => setOpen(false)} title={label}>
        {state === "loading" && <p className="text-matrix-green/40">Loading…</p>}
        {state === "error" && <p className="text-red-500/70">Couldn&apos;t load the breakdown — try again.</p>}
        {state === "ready" && dimension && <DimensionBody dimension={dimension} />}
        {state === "ready" && !dimension && (
          <p className="text-matrix-green/40 italic">No breakdown data for this dimension.</p>
        )}
      </Modal>
    </div>
  );
}
