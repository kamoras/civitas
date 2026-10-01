"use client";

import { useId, useRef, useState, type KeyboardEvent } from "react";
import Link from "next/link";
import RaceFullDetail from "@/components/elections/RaceFullDetail";
import CoverageFeed from "@/components/elections/CoverageFeed";
import { getPartyMeta } from "@/components/elections/CandidateCard";
import { candidateName, isActiveCandidate } from "@/lib/elections";
import { getScoreColor } from "@/lib/representation";
import type { RaceCoverageItem, RaceWithCandidates } from "@/types/election";

type Tab = "money" | "record" | "news";

/** Voting records for the candidates who have one: a member's
 * Representation Score, linked to their full scorecard. Everyone else is
 * listed too, marked "no scorecard" — not "no record", since the API links
 * a scorecard only on an unambiguous match and never guesses. */
function RecordPanel({ race }: { race: RaceWithCandidates }) {
  const active = race.candidates.filter(isActiveCandidate);
  const withRecord = active.filter((c) => c.incumbentRecord);
  return (
    <div>
      <p className="mb-3 text-[13px] text-ink-lo">
        A member of Congress has a Representation Score from their voting record, linked to their
        full scorecard.
      </p>
      <ul>
        {active.map((c) => (
          <li
            key={c.id}
            className="flex items-baseline justify-between gap-3 border-b border-white/[0.09] py-2.5"
          >
            <span className="flex min-w-0 flex-col">
              <span className="text-[15px] font-bold text-ink-hi">{candidateName(c)}</span>
              <span className={`font-mono text-[11px] tracking-[0.08em] ${getPartyMeta(c).color}`}>
                {getPartyMeta(c).label}
              </span>
            </span>
            {c.incumbentRecord ? (
              <Link
                href={`/politicians/${c.incumbentRecord.id}`}
                className="shrink-0 font-mono text-xs text-ink-lo hover:text-phos"
              >
                score{" "}
                <span className={getScoreColor(c.incumbentRecord.score)}>
                  {c.incumbentRecord.score.toFixed(1)}
                </span>{" "}
                · scorecard →
              </Link>
            ) : (
              <span className="shrink-0 font-mono text-xs text-ink-min">no scorecard</span>
            )}
          </li>
        ))}
      </ul>
      {withRecord.length === 0 && (
        <p className="mt-3 text-[13px] text-ink-lo">No one in this race has a Civitas scorecard.</p>
      )}
    </div>
  );
}

/** One race's research, in tabs INSIDE the contest: Money / Record / News.
 * Tabs belong here and not across the ballot's sections — a voter needs
 * every contest, but within one contest these are supplemental views of
 * the same candidates (NN/g, "Tabs, Used Right"). */
export default function RaceResearch({
  race,
  coverage,
  supersededByPrimary = false,
  newLines = false,
  resultsMode = false,
}: {
  race: RaceWithCandidates;
  coverage: RaceCoverageItem[];
  supersededByPrimary?: boolean;
  /** StateBallot.newDistrictLines — how incumbency is worded. */
  newLines?: boolean;
  /** From election day on — how incumbency is worded (incumbencyLabel). */
  resultsMode?: boolean;
}) {
  const [tab, setTab] = useState<Tab>("money");
  const base = useId();
  const tabs: { id: Tab; label: string }[] = [
    { id: "money", label: "Money" },
    { id: "record", label: "Record" },
    { id: "news", label: coverage.length ? `News ${coverage.length}` : "News" },
  ];
  const buttons = useRef<(HTMLButtonElement | null)[]>([]);

  // Arrow keys move between tabs, Home/End jump — the WAI-ARIA tabs pattern.
  function onKeyDown(e: KeyboardEvent<HTMLDivElement>) {
    const i = tabs.findIndex((t) => t.id === tab);
    const next =
      e.key === "ArrowRight"
        ? (i + 1) % tabs.length
        : e.key === "ArrowLeft"
          ? (i - 1 + tabs.length) % tabs.length
          : e.key === "Home"
            ? 0
            : e.key === "End"
              ? tabs.length - 1
              : null;
    if (next === null) return;
    e.preventDefault();
    setTab(tabs[next].id);
    buttons.current[next]?.focus();
  }

  return (
    <div>
      <div
        role="tablist"
        aria-label="Research this race"
        className="flex gap-2"
        onKeyDown={onKeyDown}
      >
        {tabs.map((t, i) => {
          const selected = t.id === tab;
          return (
            <button
              key={t.id}
              ref={(el) => {
                buttons.current[i] = el;
              }}
              type="button"
              role="tab"
              id={`${base}-${t.id}-tab`}
              aria-selected={selected}
              aria-controls={`${base}-${t.id}-panel`}
              tabIndex={selected ? 0 : -1}
              onClick={() => setTab(t.id)}
              className={`min-h-[44px] flex-1 border px-4 text-sm ${
                selected
                  ? "border-phos bg-phos font-bold text-surface-base"
                  : "border-white/25 text-ink-hi hover:border-white/50"
              }`}
            >
              {t.label}
            </button>
          );
        })}
      </div>
      {/* Every panel is rendered and the inactive ones hidden, so each
          tab's aria-controls names an element that exists. */}
      {tabs.map((t) => (
        <div
          key={t.id}
          role="tabpanel"
          id={`${base}-${t.id}-panel`}
          aria-labelledby={`${base}-${t.id}-tab`}
          tabIndex={0}
          hidden={t.id !== tab}
          className="mt-4"
        >
          {t.id === "money" && (
            <RaceFullDetail
              race={race}
              supersededByPrimary={supersededByPrimary}
              newLines={newLines}
              resultsMode={resultsMode}
            />
          )}
          {t.id === "record" && <RecordPanel race={race} />}
          {t.id === "news" && <CoverageFeed items={coverage} />}
        </div>
      ))}
    </div>
  );
}
