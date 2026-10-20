"use client";

import { MEMBERS_HREF } from "@/lib/routes";
import { useId, useRef, useState, type KeyboardEvent } from "react";
import Link from "next/link";
import RaceFullDetail from "@/components/elections/RaceFullDetail";
import CoverageFeed from "@/components/elections/CoverageFeed";
import RaceContext from "@/components/elections/ballot/RaceContext";
import { getPartyMeta } from "@/components/elections/CandidateCard";
import { candidateName, isActiveCandidate, memberRecordLabel } from "@/lib/elections";
import type { RaceCoverageItem, RaceWithCandidates } from "@/types/election";

type Tab = "money" | "record" | "news";

/** Voting records for the candidates the API links one for: a member
 * running for re-election, matched to their own record, with their
 * Representation Score and a link to their full scorecard; and a member of
 * the other chamber running here (a House member running for the Senate),
 * by the bioguide crosswalk, named as what they are now. Everyone else is
 * listed too, marked "not linked" — never "no scorecard" or "no record":
 * the API links only matches it is certain of and never guesses, so a
 * member it can't tie, or one it stops linking once the new Congress
 * sits, has a scorecard this panel doesn't link. */
function RecordPanel({ race }: { race: RaceWithCandidates }) {
  const active = race.candidates.filter(isActiveCandidate);
  const withRecord = active.filter((c) => c.incumbentRecord || c.memberRecord);
  return (
    <div>
      <p className="mb-3 text-[13px] text-ink-lo">
        A member of Congress in this race is linked to their Representation Score and full scorecard
        when the match to their record is certain: one running for re-election, or one who serves in
        the other chamber now. Anyone not linked here may still be under{" "}
        <Link href={MEMBERS_HREF} className="underline hover:text-phos">
          Politicians
        </Link>
        .
      </p>
      <ul>
        {active.map((c) => (
          <li
            key={c.id}
            className="flex items-baseline justify-between gap-3 border-b border-white/[0.09] py-2.5"
          >
            <span className="flex min-w-0 flex-col">
              <span className="text-[15px] font-semibold text-ink-hi">{candidateName(c)}</span>
              <span className={`font-mono text-[11px] tracking-[0.08em] ${getPartyMeta(c).color}`}>
                {getPartyMeta(c).label}
              </span>
            </span>
            {c.incumbentRecord ? (
              <Link
                href={`/politicians/${c.incumbentRecord.id}`}
                className="shrink-0 font-mono text-xs text-ink-lo hover:text-phos"
              >
                record and peers →
              </Link>
            ) : c.memberRecord ? (
              <Link
                href={`/politicians/${c.memberRecord.id}`}
                className="shrink-0 text-right font-mono text-xs text-ink-lo hover:text-phos"
              >
                {memberRecordLabel(c.memberRecord)} · record and peers →
              </Link>
            ) : (
              <span className="shrink-0 font-mono text-xs text-ink-min">not linked</span>
            )}
          </li>
        ))}
      </ul>
      {withRecord.length === 0 && (
        <p className="mt-3 text-[13px] text-ink-lo">
          No one in this race is linked to a Civitas scorecard.
        </p>
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
      <RaceContext race={race} newLines={newLines} resultsMode={resultsMode} />
      <div
        role="tablist"
        aria-label="Research this race"
        className="flex flex-wrap gap-2"
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
                  ? "border-phos bg-phos font-semibold text-surface-base"
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
