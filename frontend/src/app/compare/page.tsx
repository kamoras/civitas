"use client";

import { Suspense, useEffect, useState, useCallback } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import Navbar from "@/components/layout/Navbar";
import PageMasthead from "@/components/layout/PageMasthead";
import Footer from "@/components/layout/Footer";
import {
  fetchSenatorsByState,
  fetchSenator,
  fetchRepresentative,
  fetchRepresentativesByState,
  fetchStates,
  fetchRepStates,
} from "@/lib/api";
import type { StateInfo, RepStateInfo } from "@/lib/api";
import type { Senator } from "@/types/senator";
import { getScoreColor, asciiScoreBar } from "@/lib/representation";
import { formatCurrency } from "@/lib/formatting";
import { useUserState } from "@/hooks/useUserState";
import { PARTY_COLORS } from "@/lib/partyStyles";
import { BOXED_CONTROL } from "@/lib/controlStyles";

type Chamber = "senate" | "house";

// v6.5: fundingDiversity folded into fundingIndependence — no longer its
// own scored dimension (see RepresentationScore.tsx's matching comment).
const SCORE_KEYS = [
  "fundingIndependence",
  "independentVoting",
  "legislativeEffectiveness",
] as const;

type ScoreKey = (typeof SCORE_KEYS)[number];

const SCORE_LABELS: Record<ScoreKey, string> = {
  fundingIndependence: "FUNDING INDEP",
  independentVoting: "ALIGNMENT",
  legislativeEffectiveness: "LEGIS EFFECT",
};

function ScoreBar({ value, colorClass }: { value: number; colorClass: string }) {
  return (
    <span className={`font-mono text-xs tracking-tight ${colorClass}`} aria-hidden="true">
      {asciiScoreBar(value)}
    </span>
  );
}

function SenatorSelector({
  side,
  onSelect,
  selectedId,
  initialChamber,
}: {
  side: "left" | "right";
  onSelect: (senator: Senator | null, chamber: Chamber) => void;
  selectedId?: string;
  initialChamber?: Chamber;
}) {
  const [chamber, setChamber] = useState<Chamber>(initialChamber ?? "senate");
  const [senateStates, setSenateStates] = useState<StateInfo[]>([]);
  const [houseStates, setHouseStates] = useState<RepStateInfo[]>([]);
  const [selectedState, setSelectedState] = useState("");
  const [members, setMembers] = useState<Senator[]>([]);
  const [loading, setLoading] = useState(false);
  const label = side === "left" ? "LEFT" : "RIGHT";

  useEffect(() => {
    fetchStates()
      .then(setSenateStates)
      .catch(() => {});
    fetchRepStates()
      .then(setHouseStates)
      .catch(() => {});
  }, []);

  const loadMembers = useCallback((state: string, ch: Chamber) => {
    if (!state) return;
    setLoading(true);
    if (ch === "senate") {
      fetchSenatorsByState(state)
        .then(setMembers)
        .catch(() => setMembers([]))
        .finally(() => setLoading(false));
    } else {
      fetchRepresentativesByState(state, 1, 60)
        .then((res) => setMembers(res.entries))
        .catch(() => setMembers([]))
        .finally(() => setLoading(false));
    }
  }, []);

  const handleChamberToggle = (newChamber: Chamber) => {
    setChamber(newChamber);
    setSelectedState("");
    setMembers([]);
    onSelect(null, newChamber);
  };

  const stateOptions =
    chamber === "senate"
      ? senateStates.map((s) => ({ code: s.code, name: s.name }))
      : houseStates.map((s) => ({ code: s.code, name: s.name }));

  return (
    <div className="panel p-4 space-y-3">
      <div className="font-mono text-xs text-ink-lo tracking-widest">
        {label} — SELECT LEGISLATOR
      </div>

      {/* Chamber toggle */}
      <div className="flex gap-2">
        <button
          onClick={() => handleChamberToggle("senate")}
          className={`font-mono text-xs px-2 py-1 border transition-colors ${
            chamber === "senate" ? BOXED_CONTROL.selected : BOXED_CONTROL.unselected
          }`}
        >
          SEN
        </button>
        <button
          onClick={() => handleChamberToggle("house")}
          className={`font-mono text-xs px-2 py-1 border transition-colors ${
            chamber === "house" ? BOXED_CONTROL.selected : BOXED_CONTROL.unselected
          }`}
        >
          HOUSE
        </button>
      </div>

      <div>
        <label htmlFor={`state-${side}`} className="sr-only">
          Select state for {label} panel
        </label>
        <select
          id={`state-${side}`}
          value={selectedState}
          onChange={(e) => {
            setSelectedState(e.target.value);
            setMembers([]);
            onSelect(null, chamber);
            if (e.target.value) loadMembers(e.target.value, chamber);
          }}
          className="w-full bg-white/[0.03] border border-white/15 text-ink-hi px-3 py-2 font-mono text-xs focus:outline-none focus:border-signal-cyan/40"
        >
          <option value="">— SELECT STATE —</option>
          {stateOptions.map((s) => (
            <option key={s.code} value={s.code}>
              {s.code} — {s.name}
            </option>
          ))}
        </select>
      </div>

      {loading && (
        <div className="text-ink-min font-mono text-xs tracking-widest animate-pulse">
          LOADING...
        </div>
      )}

      {members.length > 0 && (
        <div className="space-y-1.5">
          {members.map((s) => (
            <button
              key={s.id}
              onClick={() => onSelect(s, chamber)}
              className={`w-full text-left px-3 py-2 border transition-colors font-mono text-xs ${
                s.id === selectedId ? BOXED_CONTROL.selected : BOXED_CONTROL.unselected
              }`}
            >
              <span className={`mr-2 ${PARTY_COLORS[s.party]}`}>[{s.party}]</span>
              {s.name}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function ComparisonTable({
  left,
  right,
  leftChamber,
  rightChamber,
}: {
  left: Senator;
  right: Senator;
  leftChamber: Chamber;
  rightChamber: Chamber;
}) {
  const leftOverall = left.representationScore.overall;
  const rightOverall = right.representationScore.overall;
  const leftColorClass = getScoreColor(leftOverall);
  const rightColorClass = getScoreColor(rightOverall);
  const leftPacPct =
    left.funding.totalRaised > 0
      ? Math.round((left.funding.totalFromPACs / left.funding.totalRaised) * 100)
      : 0;
  const rightPacPct =
    right.funding.totalRaised > 0
      ? Math.round((right.funding.totalFromPACs / right.funding.totalRaised) * 100)
      : 0;

  function winner(a: number, b: number, higherIsBetter = true) {
    // No winner markers across chambers: calibration is chamber-specific,
    // so declaring a cross-chamber "better score" asserts a like-for-like
    // comparison the methodology doesn't support (see the caveat banner).
    if (leftChamber !== rightChamber) return null;
    if (a === b) return null;
    return (higherIsBetter ? a > b : a < b) ? "left" : "right";
  }

  function WinnerTag({
    side,
    actual,
  }: {
    side: "left" | "right" | null;
    actual: "left" | "right";
  }) {
    if (side !== actual) return <span className="w-4" />;
    return (
      <span className="text-ink-hi font-mono text-xs" aria-label="better score">
        ▲
      </span>
    );
  }

  const leftScorecardUrl = `/politicians/${left.id}`;
  const rightScorecardUrl = `/politicians/${right.id}`;

  return (
    <div className="panel overflow-hidden">
      {/* Header */}
      <div className="grid grid-cols-3 border-b border-white/[0.07] bg-white/[0.03]">
        <div className="p-3 text-center">
          <div className={`font-display font-semibold text-2xl ${leftColorClass}`}>
            {leftOverall}
          </div>
          <div className={`font-mono text-xs ${PARTY_COLORS[left.party]}`}>
            [{left.party}] {left.state}
          </div>
          <div className="text-ink text-xs font-mono leading-snug mt-1">{left.name}</div>
          <div className="text-ink-min font-mono text-xs mt-0.5 uppercase tracking-wide">
            {leftChamber === "house" ? "House" : "Senate"}
          </div>
        </div>
        <div className="p-3 flex items-center justify-center">
          <span className="text-ink-min font-mono text-xs">VS</span>
        </div>
        <div className="p-3 text-center">
          <div className={`font-display font-semibold text-2xl ${rightColorClass}`}>
            {rightOverall}
          </div>
          <div className={`font-mono text-xs ${PARTY_COLORS[right.party]}`}>
            [{right.party}] {right.state}
          </div>
          <div className="text-ink text-xs font-mono leading-snug mt-1">{right.name}</div>
          <div className="text-ink-min font-mono text-xs mt-0.5 uppercase tracking-wide">
            {rightChamber === "house" ? "House" : "Senate"}
          </div>
        </div>
      </div>

      {/* Cross-chamber comparability caveat: score calibration is
          deliberately chamber-specific (PAC multiplier x3.2 Senate vs
          x1.35 House; chamber-split LES baselines), so a 70 in one
          chamber is not the same measurement as a 70 in the other —
          head-to-head "better score" markers across chambers would imply
          a like-for-like comparison the methodology doesn't support. */}
      {leftChamber !== rightChamber && (
        <div className="px-3 py-2 border-b border-signal-amber/40 bg-signal-amber/10 text-center">
          <span className="text-signal-amber font-mono text-xs uppercase tracking-wide">
            Cross-chamber comparison — scores are calibrated within each chamber, so side-by-side
            numbers are indicative, not like-for-like
          </span>
        </div>
      )}

      {/* Score metrics */}
      <div className="divide-y divide-white/[0.07]">
        {SCORE_KEYS.map((key) => {
          const lv = left.representationScore[key];
          const rv = right.representationScore[key];
          const w = winner(lv, rv);
          const lColor = getScoreColor(lv);
          const rColor = getScoreColor(rv);

          return (
            <div key={key} className="grid grid-cols-3 items-center px-3 py-2">
              <div className="flex items-center justify-end gap-1.5">
                <WinnerTag side={w} actual="left" />
                <div className="text-right">
                  <div className={`font-mono text-sm ${lColor}`}>{lv}</div>
                  <div className="hidden sm:block">
                    <ScoreBar value={lv} colorClass={lColor} />
                  </div>
                </div>
              </div>
              <div className="text-center px-1">
                <div className="text-xs text-ink-lo font-mono leading-snug tracking-wide">
                  {SCORE_LABELS[key]}
                </div>
              </div>
              <div className="flex items-center gap-1.5">
                <div className="text-left">
                  <div className={`font-mono text-sm ${rColor}`}>{rv}</div>
                  <div className="hidden sm:block">
                    <ScoreBar value={rv} colorClass={rColor} />
                  </div>
                </div>
                <WinnerTag side={w} actual="right" />
              </div>
            </div>
          );
        })}
      </div>

      {/* Funding stats */}
      <div className="border-t border-white/[0.07] bg-white/[0.03] divide-y divide-white/[0.07]">
        {[
          {
            label: "TOTAL RAISED",
            lv: formatCurrency(left.funding.totalRaised),
            rv: formatCurrency(right.funding.totalRaised),
          },
          {
            label: "PAC MONEY",
            lv: formatCurrency(left.funding.totalFromPACs),
            rv: formatCurrency(right.funding.totalFromPACs),
          },
          { label: "PAC %", lv: `${leftPacPct}%`, rv: `${rightPacPct}%` },
        ].map(({ label, lv, rv }) => (
          <div key={label} className="grid grid-cols-3 items-center px-3 py-1.5">
            <div className="text-right font-mono text-xs text-ink-lo">{lv}</div>
            <div className="text-center text-xs text-ink-min font-mono tracking-wide">{label}</div>
            <div className="text-left font-mono text-xs text-ink-lo">{rv}</div>
          </div>
        ))}
      </div>

      {/* Full scorecard links */}
      <div className="grid grid-cols-2 border-t border-white/[0.07]">
        <a
          href={leftScorecardUrl}
          className="p-3 text-center font-mono text-xs tracking-widest text-ink-lo hover:bg-signal-cyan/10 hover:text-phos transition-colors border-r border-white/[0.07]"
        >
          FULL SCORECARD →
        </a>
        <a
          href={rightScorecardUrl}
          className="p-3 text-center font-mono text-xs tracking-widest text-ink-lo hover:bg-signal-cyan/10 hover:text-phos transition-colors"
        >
          FULL SCORECARD →
        </a>
      </div>
    </div>
  );
}

function ComparePageInner() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [leftSenator, setLeftSenator] = useState<Senator | null>(null);
  const [rightSenator, setRightSenator] = useState<Senator | null>(null);
  const [leftChamber, setLeftChamber] = useState<Chamber>("senate");
  const [rightChamber, setRightChamber] = useState<Chamber>("senate");
  // Seeded from the URL rather than switched on inside the effect: whether
  // this render will hydrate is already knowable from the query string, and
  // setting it in the effect cost an extra render pass before the first
  // request even went out. searchParams is stable for the initial render,
  // which is the only one this initialiser is read on.
  const [hydrating, setHydrating] = useState(() =>
    Boolean(searchParams.get("leftId") || searchParams.get("rightId"))
  );
  const [savedState] = useUserState();
  const [savedStateName, setSavedStateName] = useState<string | null>(null);
  const [quickLoading, setQuickLoading] = useState(false);

  // Resolve the saved state code (from useUserState, SSR-safe) to a display name.
  useEffect(() => {
    if (!savedState) return;
    fetchStates()
      .then((states) => {
        const match = states.find((s) => s.code === savedState);
        if (match) setSavedStateName(match.name);
      })
      .catch(() => {});
  }, [savedState]);

  // Hydrate from URL params on mount (only when sides are not already set)
  useEffect(() => {
    const leftId = searchParams.get("leftId");
    const rightId = searchParams.get("rightId");
    const leftCh = (searchParams.get("leftChamber") ?? "senate") as Chamber;
    const rightCh = (searchParams.get("rightChamber") ?? "senate") as Chamber;

    if (!leftId && !rightId) return;

    const promises: Promise<void>[] = [];

    if (leftId) {
      const fetchFn = leftCh === "house" ? fetchRepresentative : fetchSenator;
      promises.push(
        fetchFn(leftId)
          .then((senator) => {
            setLeftSenator(senator);
            setLeftChamber(leftCh);
          })
          .catch(() => {})
      );
    }

    if (rightId) {
      const fetchFn = rightCh === "house" ? fetchRepresentative : fetchSenator;
      promises.push(
        fetchFn(rightId)
          .then((senator) => {
            setRightSenator(senator);
            setRightChamber(rightCh);
          })
          .catch(() => {})
      );
    }

    Promise.all(promises).finally(() => setHydrating(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []); // intentionally run once on mount

  const updateUrl = useCallback(
    (left: Senator | null, right: Senator | null, lCh: Chamber, rCh: Chamber) => {
      const params = new URLSearchParams();
      if (left) {
        params.set("leftId", left.id);
        params.set("leftChamber", lCh);
      }
      if (right) {
        params.set("rightId", right.id);
        params.set("rightChamber", rCh);
      }
      router.replace(params.toString() ? `?${params}` : "/compare", { scroll: false });
    },
    [router]
  );

  const handleLeft = useCallback(
    (s: Senator | null, ch: Chamber) => {
      setLeftSenator(s);
      setLeftChamber(ch);
      updateUrl(s, rightSenator, ch, rightChamber);
    },
    [rightSenator, rightChamber, updateUrl]
  );

  const handleRight = useCallback(
    (s: Senator | null, ch: Chamber) => {
      setRightSenator(s);
      setRightChamber(ch);
      updateUrl(leftSenator, s, leftChamber, ch);
    },
    [leftSenator, leftChamber, updateUrl]
  );

  // Quick-populate from saved state
  const handleQuickCompare = useCallback(() => {
    if (!savedState) return;
    setQuickLoading(true);
    fetchSenatorsByState(savedState)
      .then((senators) => {
        if (senators.length >= 2) {
          setLeftSenator(senators[0]);
          setLeftChamber("senate");
          setRightSenator(senators[1]);
          setRightChamber("senate");
          updateUrl(senators[0], senators[1], "senate", "senate");
        }
        // If fewer than 2 senators, do nothing — selectors stay empty
      })
      .catch(() => {})
      .finally(() => setQuickLoading(false));
  }, [savedState, updateUrl]);

  if (hydrating) {
    return (
      <>
        <Navbar />
        <main id="main-content" tabIndex={-1} className="pt-[var(--header-clearance)] pb-16 px-4">
          <div className="max-w-5xl mx-auto">
            <div className="panel p-8 text-center">
              <div className="font-mono text-xs text-ink-min tracking-widest animate-pulse">
                LOADING...
              </div>
            </div>
          </div>
        </main>
        <Footer />
      </>
    );
  }

  return (
    <>
      <Navbar />
      <main id="main-content" tabIndex={-1} className="pt-[var(--header-clearance)] pb-16 px-4">
        <div className="max-w-5xl mx-auto">
          <PageMasthead
            className="mb-8"
            eyebrow="Compare · two legislators, side by side"
            title="Compare legislators"
          >
            <p>
              Select two legislators to compare their representation scores, funding sources, and
              voting independence side by side.
            </p>
          </PageMasthead>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-6">
            <SenatorSelector
              side="left"
              onSelect={handleLeft}
              selectedId={leftSenator?.id}
              initialChamber={leftChamber}
            />
            <SenatorSelector
              side="right"
              onSelect={handleRight}
              selectedId={rightSenator?.id}
              initialChamber={rightChamber}
            />
          </div>

          {leftSenator && rightSenator ? (
            <ComparisonTable
              left={leftSenator}
              right={rightSenator}
              leftChamber={leftChamber}
              rightChamber={rightChamber}
            />
          ) : (
            <div className="panel p-8 text-center space-y-4">
              <div className="font-mono text-sm text-ink-min">
                {!leftSenator && !rightSenator
                  ? "SELECT TWO LEGISLATORS ABOVE TO COMPARE"
                  : "SELECT A SECOND LEGISLATOR TO COMPARE"}
              </div>
              {!leftSenator && !rightSenator && savedState && (
                <button
                  onClick={handleQuickCompare}
                  disabled={quickLoading}
                  className="mt-2 px-4 py-2 border border-signal-cyan/40 text-signal-cyan font-mono text-xs tracking-widest hover:bg-signal-cyan/10 hover:border-signal-cyan/40 transition-colors
                             disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  {quickLoading
                    ? "LOADING..."
                    : `COMPARE MY SENATORS FROM ${savedStateName ?? savedState}`}
                </button>
              )}
            </div>
          )}
        </div>
      </main>
      <Footer />
    </>
  );
}

export default function ComparePage() {
  return (
    <Suspense>
      <ComparePageInner />
    </Suspense>
  );
}
