"use client";

import { Suspense, useEffect, useState, useCallback } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import MatrixRain from "@/components/effects/MatrixRain";
import Navbar from "@/components/layout/Navbar";
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
import { calculateOverallScore, getScoreColor } from "@/lib/corruption";
import { formatCurrency } from "@/lib/formatting";
import { useScoreWeights } from "@/hooks/useConfig";

type Chamber = "senate" | "house";

const SCORE_KEYS = [
  "fundingIndependence",
  "promisePersistence",
  "independentVoting",
  "fundingDiversity",
  "legislativeEffectiveness",
] as const;

type ScoreKey = (typeof SCORE_KEYS)[number];

const SCORE_LABELS: Record<ScoreKey, string> = {
  fundingIndependence: "FUNDING INDEP",
  promisePersistence: "PROMISE PERSIST",
  independentVoting: "INDEPENDENT VOTE",
  fundingDiversity: "FUNDING DIVERS",
  legislativeEffectiveness: "LEGIS EFFECT",
};

const PARTY_COLORS: Record<string, string> = {
  D: "text-dem-blue",
  R: "text-rep-red",
  I: "text-ind-purple",
};

function ScoreBar({ value, colorClass }: { value: number; colorClass: string }) {
  const filled = Math.round(value / 5);
  const empty = 20 - filled;
  const bar = "█".repeat(filled) + "░".repeat(empty);
  return (
    <span className={`font-mono text-xs tracking-tight ${colorClass}`} aria-hidden="true">
      {bar}
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
    fetchStates().then(setSenateStates).catch(() => {});
    fetchRepStates().then(setHouseStates).catch(() => {});
  }, []);

  const loadMembers = useCallback(
    (state: string, ch: Chamber) => {
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
    },
    [],
  );

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
    <div className="terminal-window p-4 space-y-3">
      <div className="font-mono text-xs text-neon-cyan/60 tracking-widest">
        {label} — SELECT LEGISLATOR
      </div>

      {/* Chamber toggle */}
      <div className="flex gap-2">
        <button
          onClick={() => handleChamberToggle("senate")}
          className={`font-mono text-xs px-2 py-1 border transition-colors ${
            chamber === "senate"
              ? "border-neon-cyan/60 bg-neon-cyan/10 text-neon-cyan"
              : "border-matrix-green/20 text-matrix-green/50 hover:border-matrix-green/40 hover:text-matrix-green/70"
          }`}
        >
          SEN
        </button>
        <button
          onClick={() => handleChamberToggle("house")}
          className={`font-mono text-xs px-2 py-1 border transition-colors ${
            chamber === "house"
              ? "border-neon-cyan/60 bg-neon-cyan/10 text-neon-cyan"
              : "border-matrix-green/20 text-matrix-green/50 hover:border-matrix-green/40 hover:text-matrix-green/70"
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
          className="w-full bg-matrix-dark-green/20 border border-matrix-green/30 text-matrix-green
                     px-3 py-2 font-mono text-xs focus:outline-none focus:border-neon-cyan/50"
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
        <div className="text-matrix-green/40 font-mono text-xs tracking-widest animate-pulse">
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
                s.id === selectedId
                  ? "border-neon-cyan/60 bg-neon-cyan/10 text-neon-cyan"
                  : "border-matrix-green/20 hover:border-matrix-green/40 text-matrix-green/80"
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
  const weights = useScoreWeights();
  const leftOverall = calculateOverallScore(left.representationScore, weights);
  const rightOverall = calculateOverallScore(right.representationScore, weights);
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
      <span className="text-matrix-green font-mono text-xs" aria-label="better score">
        ▲
      </span>
    );
  }

  const leftScorecardUrl =
    leftChamber === "house"
      ? `/scorecard?branch=house&state=${left.state}&senator=${left.id}`
      : `/scorecard?branch=senate&state=${left.state}&senator=${left.id}`;
  const rightScorecardUrl =
    rightChamber === "house"
      ? `/scorecard?branch=house&state=${right.state}&senator=${right.id}`
      : `/scorecard?branch=senate&state=${right.state}&senator=${right.id}`;

  return (
    <div className="terminal-window overflow-hidden">
      {/* Header */}
      <div className="grid grid-cols-3 border-b border-matrix-green/20 bg-matrix-dark-green/20">
        <div className="p-3 text-center">
          <div className={`font-pixel text-2xl ${leftColorClass}`}>{leftOverall}</div>
          <div className={`font-pixel text-xs ${PARTY_COLORS[left.party]}`}>
            [{left.party}] {left.state}
          </div>
          <div className="text-matrix-green/80 text-xs font-pixel leading-snug mt-1">
            {left.name}
          </div>
          <div className="text-matrix-green/30 font-mono text-[10px] mt-0.5 uppercase tracking-wide">
            {leftChamber === "house" ? "House" : "Senate"}
          </div>
        </div>
        <div className="p-3 flex items-center justify-center">
          <span className="text-matrix-green/30 font-pixel text-xs">VS</span>
        </div>
        <div className="p-3 text-center">
          <div className={`font-pixel text-2xl ${rightColorClass}`}>{rightOverall}</div>
          <div className={`font-pixel text-xs ${PARTY_COLORS[right.party]}`}>
            [{right.party}] {right.state}
          </div>
          <div className="text-matrix-green/80 text-xs font-pixel leading-snug mt-1">
            {right.name}
          </div>
          <div className="text-matrix-green/30 font-mono text-[10px] mt-0.5 uppercase tracking-wide">
            {rightChamber === "house" ? "House" : "Senate"}
          </div>
        </div>
      </div>

      {/* Score metrics */}
      <div className="divide-y divide-matrix-green/10">
        {SCORE_KEYS.map((key) => {
          const lv = left.representationScore[key];
          const rv = right.representationScore[key];
          const w = winner(lv, rv);
          const lColor =
            lv >= 70 ? "text-matrix-green" : lv >= 40 ? "text-yellow-500" : "text-red-500";
          const rColor =
            rv >= 70 ? "text-matrix-green" : rv >= 40 ? "text-yellow-500" : "text-red-500";

          return (
            <div key={key} className="grid grid-cols-3 items-center px-3 py-2">
              <div className="flex items-center justify-end gap-1.5">
                <WinnerTag side={w} actual="left" />
                <div className="text-right">
                  <div className={`font-pixel text-sm ${lColor}`}>{lv}</div>
                  <div className="hidden sm:block">
                    <ScoreBar value={lv} colorClass={lColor} />
                  </div>
                </div>
              </div>
              <div className="text-center px-1">
                <div className="text-[10px] text-matrix-green/50 font-mono leading-snug tracking-wide">
                  {SCORE_LABELS[key]}
                </div>
              </div>
              <div className="flex items-center gap-1.5">
                <div className="text-left">
                  <div className={`font-pixel text-sm ${rColor}`}>{rv}</div>
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
      <div className="border-t border-matrix-green/20 bg-matrix-dark-green/10 divide-y divide-matrix-green/10">
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
            <div className="text-right font-mono text-xs text-neon-pink/60">{lv}</div>
            <div className="text-center text-[10px] text-matrix-green/40 font-mono tracking-wide">{label}</div>
            <div className="text-left font-mono text-xs text-neon-pink/60">{rv}</div>
          </div>
        ))}
      </div>

      {/* Full scorecard links */}
      <div className="grid grid-cols-2 border-t border-matrix-green/20">
        <a
          href={leftScorecardUrl}
          className="p-3 text-center font-mono text-xs tracking-widest text-neon-cyan/60 hover:bg-neon-cyan/5 hover:text-neon-cyan transition-colors border-r border-matrix-green/20"
        >
          FULL SCORECARD →
        </a>
        <a
          href={rightScorecardUrl}
          className="p-3 text-center font-mono text-xs tracking-widest text-neon-cyan/60 hover:bg-neon-cyan/5 hover:text-neon-cyan transition-colors"
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
  const [hydrating, setHydrating] = useState(false);
  const [savedState, setSavedState] = useState<string | null>(null);
  const [savedStateName, setSavedStateName] = useState<string | null>(null);
  const [quickLoading, setQuickLoading] = useState(false);

  // Read saved user state from localStorage (SSR-safe)
  useEffect(() => {
    const stored = localStorage.getItem("civitas_user_state");
    if (stored) {
      setSavedState(stored);
      // Resolve state name for display
      fetchStates()
        .then((states) => {
          const match = states.find((s) => s.code === stored);
          if (match) setSavedStateName(match.name);
        })
        .catch(() => {});
    }
  }, []);

  // Hydrate from URL params on mount (only when sides are not already set)
  useEffect(() => {
    const leftId = searchParams.get("leftId");
    const rightId = searchParams.get("rightId");
    const leftCh = (searchParams.get("leftChamber") ?? "senate") as Chamber;
    const rightCh = (searchParams.get("rightChamber") ?? "senate") as Chamber;

    if (!leftId && !rightId) return;

    setHydrating(true);

    const promises: Promise<void>[] = [];

    if (leftId) {
      const fetchFn = leftCh === "house" ? fetchRepresentative : fetchSenator;
      promises.push(
        fetchFn(leftId)
          .then((senator) => {
            setLeftSenator(senator);
            setLeftChamber(leftCh);
          })
          .catch(() => {}),
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
          .catch(() => {}),
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
    [router],
  );

  const handleLeft = useCallback(
    (s: Senator | null, ch: Chamber) => {
      setLeftSenator(s);
      setLeftChamber(ch);
      updateUrl(s, rightSenator, ch, rightChamber);
    },
    [rightSenator, rightChamber, updateUrl],
  );

  const handleRight = useCallback(
    (s: Senator | null, ch: Chamber) => {
      setRightSenator(s);
      setRightChamber(ch);
      updateUrl(leftSenator, s, leftChamber, ch);
    },
    [leftSenator, leftChamber, updateUrl],
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
        <MatrixRain />
        <Navbar />
        <main id="main-content" tabIndex={-1} className="pt-24 pb-16 px-4">
          <div className="max-w-5xl mx-auto">
            <div className="terminal-window p-8 text-center">
              <div className="font-mono text-xs text-matrix-green/40 tracking-widest animate-pulse">
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
      <MatrixRain />
      <Navbar />
      <main id="main-content" tabIndex={-1} className="pt-24 pb-16 px-4">
        <div className="max-w-5xl mx-auto">
          <div className="text-center mb-8">
            <h1 className="font-pixel text-xl sm:text-3xl text-matrix-green tracking-widest mb-2">
              COMPARE LEGISLATORS
            </h1>
            <p className="text-matrix-green/40 text-sm max-w-xl mx-auto">
              Select two legislators to compare their representation scores, funding sources, and
              voting independence side by side.
            </p>
          </div>

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
            <div className="terminal-window p-8 text-center space-y-4">
              <div className="font-pixel text-sm text-matrix-green/40">
                {!leftSenator && !rightSenator
                  ? "SELECT TWO LEGISLATORS ABOVE TO COMPARE"
                  : "SELECT A SECOND LEGISLATOR TO COMPARE"}
              </div>
              {!leftSenator && !rightSenator && savedState && (
                <button
                  onClick={handleQuickCompare}
                  disabled={quickLoading}
                  className="mt-2 px-4 py-2 border border-neon-cyan/40 text-neon-cyan font-mono text-xs tracking-widest
                             hover:bg-neon-cyan/10 hover:border-neon-cyan/70 transition-colors
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
