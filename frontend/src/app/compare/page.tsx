"use client";

import { Suspense, useEffect, useState, useCallback } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import MatrixRain from "@/components/effects/MatrixRain";
import Navbar from "@/components/layout/Navbar";
import Footer from "@/components/layout/Footer";
import { usePlainLanguage } from "@/context/PlainLanguageContext";
import { fetchSenatorsByState, fetchStates, StateInfo } from "@/lib/api";
import type { Senator } from "@/types/senator";
import { calculateOverallScore, getScoreColor } from "@/lib/corruption";
import { formatCurrency } from "@/lib/formatting";
import { useScoreWeights } from "@/hooks/useConfig";
import type { ScoreKey } from "@/lib/plainLanguage";

const SCORE_KEYS: ScoreKey[] = [
  "fundingIndependence",
  "promisePersistence",
  "independentVoting",
  "fundingDiversity",
  "legislativeEffectiveness",
];

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
}: {
  side: "left" | "right";
  onSelect: (senator: Senator | null) => void;
  selectedId?: string;
}) {
  const [states, setStates] = useState<StateInfo[]>([]);
  const [selectedState, setSelectedState] = useState("");
  const [senators, setSenators] = useState<Senator[]>([]);
  const [loading, setLoading] = useState(false);
  const label = side === "left" ? "LEFT" : "RIGHT";

  useEffect(() => {
    fetchStates().then(setStates).catch(() => {});
  }, []);

  const loadSenators = useCallback((state: string) => {
    if (!state) return;
    setLoading(true);
    fetchSenatorsByState(state)
      .then(setSenators)
      .catch(() => setSenators([]))
      .finally(() => setLoading(false));
  }, []);

  return (
    <div className="terminal-window p-4 space-y-3">
      <div className="font-pixel text-[10px] text-neon-cyan/60 tracking-widest">
        {label} — SELECT LEGISLATOR
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
            setSenators([]);
            onSelect(null);
            if (e.target.value) loadSenators(e.target.value);
          }}
          className="w-full bg-matrix-dark-green/20 border border-matrix-green/30 text-matrix-green
                     px-3 py-2 font-pixel text-xs focus:outline-none focus:border-neon-cyan/50"
        >
          <option value="">— SELECT STATE —</option>
          {states.map((s) => (
            <option key={s.code} value={s.code}>
              {s.code} — {s.name}
            </option>
          ))}
        </select>
      </div>

      {loading && (
        <div className="text-matrix-green/40 font-pixel text-[10px] animate-pulse">
          LOADING...
        </div>
      )}

      {senators.length > 0 && (
        <div className="space-y-1.5">
          {senators.map((s) => (
            <button
              key={s.id}
              onClick={() => onSelect(s)}
              className={`w-full text-left px-3 py-2 border transition-colors font-pixel text-xs ${
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
}: {
  left: Senator;
  right: Senator;
}) {
  const { terms } = usePlainLanguage();
  const weights = useScoreWeights();
  const leftOverall = calculateOverallScore(left.representationScore, weights);
  const rightOverall = calculateOverallScore(right.representationScore, weights);
  const leftColorClass = getScoreColor(leftOverall);
  const rightColorClass = getScoreColor(rightOverall);
  const leftPacPct = left.funding.totalRaised > 0
    ? Math.round((left.funding.totalFromPACs / left.funding.totalRaised) * 100)
    : 0;
  const rightPacPct = right.funding.totalRaised > 0
    ? Math.round((right.funding.totalFromPACs / right.funding.totalRaised) * 100)
    : 0;

  function winner(a: number, b: number, higherIsBetter = true) {
    if (a === b) return null;
    return (higherIsBetter ? a > b : a < b) ? "left" : "right";
  }

  function WinnerTag({ side, actual }: { side: "left" | "right" | null; actual: "left" | "right" }) {
    if (side !== actual) return <span className="w-4" />;
    return <span className="text-matrix-green font-pixel text-[10px]" aria-label="better score">▲</span>;
  }

  return (
    <div className="terminal-window overflow-hidden">
      {/* Header */}
      <div className="grid grid-cols-3 border-b border-matrix-green/20 bg-matrix-dark-green/20">
        <div className="p-3 text-center">
          <div className={`font-pixel text-2xl ${leftColorClass}`}>{leftOverall}</div>
          <div className={`font-pixel text-xs ${PARTY_COLORS[left.party]}`}>[{left.party}] {left.state}</div>
          <div className="text-matrix-green/80 text-xs font-pixel leading-snug mt-1">{left.name}</div>
        </div>
        <div className="p-3 flex items-center justify-center">
          <span className="text-matrix-green/30 font-pixel text-xs">VS</span>
        </div>
        <div className="p-3 text-center">
          <div className={`font-pixel text-2xl ${rightColorClass}`}>{rightOverall}</div>
          <div className={`font-pixel text-xs ${PARTY_COLORS[right.party]}`}>[{right.party}] {right.state}</div>
          <div className="text-matrix-green/80 text-xs font-pixel leading-snug mt-1">{right.name}</div>
        </div>
      </div>

      {/* Score metrics */}
      <div className="divide-y divide-matrix-green/10">
        {SCORE_KEYS.map((key) => {
          const lv = left.representationScore[key];
          const rv = right.representationScore[key];
          const w = winner(lv, rv);
          const t = terms(key);
          const lColor = lv >= 70 ? "text-matrix-green" : lv >= 40 ? "text-yellow-500" : "text-red-500";
          const rColor = rv >= 70 ? "text-matrix-green" : rv >= 40 ? "text-yellow-500" : "text-red-500";

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
                <div className="text-[9px] text-matrix-green/50 font-pixel leading-snug">
                  {t.shortLabel || t.label}
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
          { label: "TOTAL RAISED", lv: formatCurrency(left.funding.totalRaised), rv: formatCurrency(right.funding.totalRaised) },
          { label: "PAC MONEY", lv: formatCurrency(left.funding.totalFromPACs), rv: formatCurrency(right.funding.totalFromPACs) },
          { label: "PAC %", lv: `${leftPacPct}%`, rv: `${rightPacPct}%` },
        ].map(({ label, lv, rv }) => (
          <div key={label} className="grid grid-cols-3 items-center px-3 py-1.5">
            <div className="text-right font-pixel text-[10px] text-neon-pink/60">{lv}</div>
            <div className="text-center text-[9px] text-matrix-green/40 font-pixel">{label}</div>
            <div className="text-left font-pixel text-[10px] text-neon-pink/60">{rv}</div>
          </div>
        ))}
      </div>

      {/* Full scorecard links */}
      <div className="grid grid-cols-2 border-t border-matrix-green/20">
        <a
          href={`/scorecard?branch=senate&state=${left.state}&senator=${left.id}`}
          className="p-3 text-center font-pixel text-[10px] text-neon-cyan/60 hover:bg-neon-cyan/5 hover:text-neon-cyan transition-colors border-r border-matrix-green/20"
        >
          FULL SCORECARD →
        </a>
        <a
          href={`/scorecard?branch=senate&state=${right.state}&senator=${right.id}`}
          className="p-3 text-center font-pixel text-[10px] text-neon-cyan/60 hover:bg-neon-cyan/5 hover:text-neon-cyan transition-colors"
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

  const updateUrl = useCallback(
    (left: Senator | null, right: Senator | null) => {
      const params = new URLSearchParams();
      if (left) params.set("leftId", left.id);
      if (right) params.set("rightId", right.id);
      router.replace(params.toString() ? `?${params}` : "/compare", { scroll: false });
    },
    [router],
  );

  const handleLeft = useCallback(
    (s: Senator | null) => {
      setLeftSenator(s);
      updateUrl(s, rightSenator);
    },
    [rightSenator, updateUrl],
  );

  const handleRight = useCallback(
    (s: Senator | null) => {
      setRightSenator(s);
      updateUrl(leftSenator, s);
    },
    [leftSenator, updateUrl],
  );

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
              Select two senators to compare their representation scores, funding sources, and voting independence side by side.
            </p>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-6">
            <SenatorSelector
              side="left"
              onSelect={handleLeft}
              selectedId={leftSenator?.id}
            />
            <SenatorSelector
              side="right"
              onSelect={handleRight}
              selectedId={rightSenator?.id}
            />
          </div>

          {leftSenator && rightSenator ? (
            <ComparisonTable left={leftSenator} right={rightSenator} />
          ) : (
            <div className="terminal-window p-8 text-center">
              <div className="font-pixel text-sm text-matrix-green/40">
                {!leftSenator && !rightSenator
                  ? "SELECT TWO LEGISLATORS ABOVE TO COMPARE"
                  : "SELECT A SECOND LEGISLATOR TO COMPARE"}
              </div>
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
