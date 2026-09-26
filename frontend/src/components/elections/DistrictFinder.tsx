"use client";

import { useMemo, useState } from "react";
import type { RaceWithCandidates } from "@/types/election";

/**
 * Find your district by pointing at where you live.
 *
 * An address box lived on this page until 2026-09 and was removed: the
 * project never collects a visitor's location. What replaced it was a
 * text filter, which is better but still a chore — you have to know
 * what to type, and typing is the part people skip.
 *
 * Counties are the right unit because a person knows theirs without
 * looking it up, where almost nobody knows their district NUMBER. The
 * index is built from the rows already on the page (every race carries
 * its county list), so this needs no new data, no lookup service and no
 * network call.
 *
 * Nationally 13% of counties span more than one district (409 of
 * 3,142), so a county cannot always answer on its own — those offer the
 * two or three districts as a second tap rather than sending the reader
 * away. Nothing is typed, sent or stored either way.
 */
export default function DistrictFinder({
  races,
  onPick,
  picked,
}: {
  races: RaceWithCandidates[];
  /** Null clears the choice and shows every district again. */
  onPick: (districtId: string | null) => void;
  picked: string | null;
}) {
  const [letter, setLetter] = useState<string | null>(null);

  const { byCounty, letters } = useMemo(() => {
    const index = new Map<string, string[]>();
    for (const r of races) {
      if (r.district == null) continue;
      const id = r.id;
      for (const raw of r.counties ?? []) {
        // "Effingham County (part)" and "Effingham County" are the same
        // place — the "(part)" suffix is exactly the signal that this
        // county spans districts, which the tap-through then shows.
        const name = raw.replace(/\s*\(part\)\s*$/i, "").trim();
        if (!name) continue;
        const prev = index.get(name) ?? [];
        if (!prev.includes(id)) index.set(name, [...prev, id]);
      }
    }
    const ls = [...new Set([...index.keys()].map((c) => c[0]?.toUpperCase()).filter(Boolean))];
    ls.sort();
    return { byCounty: index, letters: ls };
  }, [races]);

  const districtLabel = (id: string) => {
    const r = races.find((x) => x.id === id);
    return r?.district != null ? `${r.state}-${r.district}` : id;
  };

  if (byCounty.size === 0) return null;

  const shown = letter
    ? [...byCounty.keys()].filter((c) => c[0]?.toUpperCase() === letter).sort()
    : [];

  return (
    <div className="mb-4 border border-white/15 p-3">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <p className="font-mono text-xs tracking-[0.1em] text-phos">FIND YOUR DISTRICT</p>
        {picked && (
          <button
            type="button"
            onClick={() => {
              onPick(null);
              setLetter(null);
            }}
            className="font-mono text-[11px] text-ink-lo underline hover:text-phos"
          >
            show all districts
          </button>
        )}
      </div>
      <p className="mt-1 font-mono text-[11px] leading-relaxed text-ink-min">
        Pick the county you live in. Nothing is typed, sent or stored — it only changes what you
        see.
      </p>

      <div className="mt-3 flex flex-wrap gap-1" role="group" aria-label="County initial">
        {letters.map((l) => (
          <button
            key={l}
            type="button"
            aria-pressed={letter === l}
            onClick={() => setLetter(letter === l ? null : l)}
            className={`h-7 w-7 border font-mono text-xs ${
              letter === l
                ? "border-phos bg-phos/15 text-phos"
                : "border-white/15 text-ink-lo hover:border-phos/60 hover:text-phos"
            }`}
          >
            {l}
          </button>
        ))}
      </div>

      {letter && (
        <ul className="mt-3 flex flex-wrap gap-1.5">
          {shown.map((county) => {
            const ids = byCounty.get(county) ?? [];
            const split = ids.length > 1;
            return (
              <li key={county}>
                {split ? (
                  <span className="inline-flex flex-wrap items-center gap-1 border border-white/15 px-2 py-1">
                    <span className="font-mono text-[11px] text-ink-lo">{county}</span>
                    <span className="font-mono text-[10px] text-ink-min">split:</span>
                    {ids.map((id) => (
                      <button
                        key={id}
                        type="button"
                        onClick={() => onPick(id)}
                        className="border border-white/20 px-1.5 font-mono text-[10px] text-ink-hi hover:border-phos hover:text-phos"
                      >
                        {districtLabel(id)}
                      </button>
                    ))}
                  </span>
                ) : (
                  <button
                    type="button"
                    onClick={() => onPick(ids[0])}
                    className={`border px-2 py-1 font-mono text-[11px] ${
                      picked === ids[0]
                        ? "border-phos bg-phos/10 text-phos"
                        : "border-white/15 text-ink-lo hover:border-phos/60 hover:text-phos"
                    }`}
                  >
                    {county}
                  </button>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
