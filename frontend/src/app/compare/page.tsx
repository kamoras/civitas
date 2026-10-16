"use client";

import Link from "next/link";
import { Suspense, useEffect, useState, useCallback } from "react";
import { useSearchParams } from "next/navigation";
import Navbar from "@/components/layout/Navbar";
import PageMasthead from "@/components/layout/PageMasthead";
import Footer from "@/components/layout/Footer";
import PageFallback from "@/components/layout/PageFallback";
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
import { displayScore, formatCurrency } from "@/lib/formatting";
import { PARTY_COLORS } from "@/lib/partyStyles";
import { BOXED_CONTROL } from "@/lib/controlStyles";
import VotingAgreement from "@/components/compare/VotingAgreement";
import { useYourState } from "@/hooks/useYourState";

type Chamber = "senate" | "house";

// v6.5: fundingDiversity folded into fundingIndependence — no longer its
// own scored dimension.
const SCORE_KEYS = [
  "fundingIndependence",
  "constituentAlignment",
  "legislativeEffectiveness",
] as const;

type ScoreKey = (typeof SCORE_KEYS)[number];

const SCORE_LABELS: Record<ScoreKey, string> = {
  fundingIndependence: "FUNDING INDEP",
  constituentAlignment: "ALIGNMENT",
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
  initialState,
}: {
  side: "left" | "right";
  onSelect: (senator: Senator | null, chamber: Chamber) => void;
  selectedId?: string;
  initialChamber?: Chamber;
  /** The state of a member preselected from the URL (a scorecard's
   *  "Compare with another member" link), so the panel shows them chosen
   *  rather than an empty "Select a state". */
  initialState?: string;
}) {
  const [chamber, setChamber] = useState<Chamber>(initialChamber ?? "senate");
  const [senateStates, setSenateStates] = useState<StateInfo[]>([]);
  const [houseStates, setHouseStates] = useState<RepStateInfo[]>([]);
  const [selectedState, setSelectedState] = useState(initialState ?? "");
  // Without a preselected member, the panel opens on the state chosen in
  // the header for this tab (useYourState) until the reader picks here.
  const yours = useYourState();
  const [stateTouched, setStateTouched] = useState(Boolean(initialState));
  const shownState = stateTouched ? selectedState : yours.state;
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

  // Load the preselected member's state once, on mount; later changes come
  // through the select, which loads on its own.
  useEffect(() => {
    if (initialState) loadMembers(initialState, initialChamber ?? "senate");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // The header's state, once it can be read, and again on a chamber change.
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- starts a fetch for a state chosen outside this component
    if (!stateTouched && yours.state) loadMembers(yours.state, chamber);
  }, [stateTouched, yours.state, chamber, loadMembers]);

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
        {label} · SELECT LEGISLATOR
      </div>

      {/* Chamber toggle */}
      <div className="flex gap-2">
        <button
          onClick={() => handleChamberToggle("senate")}
          aria-pressed={chamber === "senate"}
          className={`font-mono text-xs px-2 py-1 border transition-colors ${
            chamber === "senate" ? BOXED_CONTROL.selected : BOXED_CONTROL.unselected
          }`}
        >
          SEN
        </button>
        <button
          onClick={() => handleChamberToggle("house")}
          aria-pressed={chamber === "house"}
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
          value={shownState}
          onChange={(e) => {
            setStateTouched(true);
            setSelectedState(e.target.value);
            if (e.target.value) yours.setState(e.target.value);
            setMembers([]);
            onSelect(null, chamber);
            if (e.target.value) loadMembers(e.target.value, chamber);
          }}
          className="w-full bg-white/[0.03] border border-white/15 text-ink-hi px-3 py-2 font-mono text-xs focus:outline-none focus:border-signal-cyan/40"
        >
          <option value="">Select a state</option>
          {stateOptions.map((s) => (
            <option key={s.code} value={s.code}>
              {s.code} · {s.name}
            </option>
          ))}
        </select>
      </div>

      {loading && (
        <div className="text-ink-min font-mono text-xs tracking-widest pulse-subtle">
          LOADING...
        </div>
      )}

      {members.length > 0 && (
        <div className="space-y-1.5">
          {members.map((s) => (
            <button
              key={s.id}
              onClick={() => onSelect(s, chamber)}
              aria-pressed={s.id === selectedId}
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

/**
 * Comparisons that need no picking: both senators of a state, or the member
 * already chosen against their state's senators. Navigation over data the
 * site has, never an input (AGENTS.md §8).
 */
function ComparePresets({
  chosen,
  onPair,
}: {
  /** The one member chosen so far, if exactly one side is filled. */
  chosen: { member: Senator; chamber: Chamber } | null;
  onPair: (left: Senator, right: Senator, lCh: Chamber, rCh: Chamber) => void;
}) {
  const [states, setStates] = useState<StateInfo[]>([]);
  const [state, setState] = useState("");
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  // Keyed by the member they were fetched for, so a list fetched for an
  // earlier choice never shows beside a later one.
  const [fetched, setFetched] = useState<{ for: string; list: Senator[] } | null>(null);

  useEffect(() => {
    fetchStates()
      .then(setStates)
      .catch(() => {});
  }, []);

  // The chosen member's state's senators, other than the member.
  const chosenId = chosen?.member.id;
  const chosenState = chosen?.member.state;
  useEffect(() => {
    if (!chosenState || !chosenId) return;
    let live = true;
    fetchSenatorsByState(chosenState)
      .then((list) => {
        if (live) setFetched({ for: chosenId, list: list.filter((s) => s.id !== chosenId) });
      })
      .catch(() => {});
    return () => {
      live = false;
    };
  }, [chosenId, chosenState]);

  const colleagues = fetched && fetched.for === chosenId ? fetched.list : [];

  const pairState = (code: string) => {
    setState(code);
    setFailed(false);
    if (!code) return;
    setBusy(true);
    fetchSenatorsByState(code)
      .then((list) => {
        if (list.length >= 2) onPair(list[0], list[1], "senate", "senate");
        else setFailed(true);
      })
      .catch(() => setFailed(true))
      .finally(() => setBusy(false));
  };

  return (
    <div className="panel mb-4 flex flex-col gap-3 p-4">
      <div className="font-mono text-xs tracking-widest text-ink-lo">QUICK COMPARISONS</div>
      <div className="flex flex-wrap items-center gap-2">
        <label htmlFor="preset-state" className="font-mono text-xs text-ink">
          Both senators from
        </label>
        <select
          id="preset-state"
          value={state}
          onChange={(e) => pairState(e.target.value)}
          className="bg-white/[0.03] border border-white/15 text-ink-hi px-3 py-2 font-mono text-xs focus:outline-none focus:border-signal-cyan/40"
        >
          <option value="">Select a state</option>
          {states
            .filter((s) => s.senatorCount >= 2)
            .map((s) => (
              <option key={s.code} value={s.code}>
                {s.code} · {s.name}
              </option>
            ))}
        </select>
        {busy && <span className="font-mono text-xs text-ink-min">LOADING…</span>}
        {failed && (
          <span role="alert" className="font-mono text-xs text-signal-red">
            Could not load that state&apos;s senators.
          </span>
        )}
      </div>
      {chosen && colleagues.length > 0 && (
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-mono text-xs text-ink">
            {chosen.member.name} against {chosen.chamber === "senate" ? "the other" : "a"}{" "}
            {chosen.member.state} senator:
          </span>
          {colleagues.map((s) => (
            <button
              key={s.id}
              onClick={() => onPair(chosen.member, s, chosen.chamber, "senate")}
              className={`font-mono text-xs px-3 py-1.5 border transition-colors ${BOXED_CONTROL.unselected}`}
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
  const leftOverall = displayScore(left.representationScore.overall);
  const rightOverall = displayScore(right.representationScore.overall);
  const leftColorClass = getScoreColor(leftOverall);
  const rightColorClass = getScoreColor(rightOverall);
  const leftPacPct = Math.round(left.funding.pacSharePct);
  const rightPacPct = Math.round(right.funding.pacSharePct);

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
          deliberately chamber-specific (each chamber's own measured
          references — PAC-share median, LES median and spread, break-rate
          expectation — see population_reference.py), so a 70 in one
          chamber is not the same measurement as a 70 in the other —
          head-to-head "better score" markers across chambers would imply
          a like-for-like comparison the methodology doesn't support. */}
      {leftChamber !== rightChamber && (
        <div className="px-3 py-2 border-b border-signal-amber/40 bg-signal-amber/10 text-center">
          <span className="text-signal-amber font-mono text-xs uppercase tracking-wide">
            Cross-chamber comparison: scores are calibrated within each chamber, so side-by-side
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

      {leftChamber === rightChamber && (
        <VotingAgreement
          chamber={leftChamber}
          left={{ id: left.id, name: left.name }}
          right={{ id: right.id, name: right.name }}
        />
      )}

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
            lv: formatCurrency(left.funding.totalFromPacs),
            rv: formatCurrency(right.funding.totalFromPacs),
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

// The masthead's line, sent with the server's fallback too (PageFallback).
const COMPARE_EYEBROW = "Compare · two legislators, side by side";
const COMPARE_TITLE = "Compare legislators";
const COMPARE_LEDE = (
  <p>
    Pick two legislators, or start from a quick comparison, to see their representation scores,
    funding sources, and constituent alignment side by side. Presidents compare on the{" "}
    <Link
      href="/compare/presidents"
      className="underline decoration-white/30 underline-offset-4 hover:text-phos"
    >
      president comparison
    </Link>
    .
  </p>
);

function ComparePageInner() {
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
      // Not router.replace: on this statically prerendered route it does
      // nothing once the page was loaded with a query string, so the address
      // bar froze on the first pair (AGENTS.md, "Client-side URL state").
      window.history.replaceState(null, "", params.toString() ? `?${params}` : "/compare");
    },
    []
  );
  // Bumped when a preset fills both sides, so the pickers remount showing
  // the new pair rather than the state each was last left on.
  const [presetVersion, setPresetVersion] = useState(0);
  const handlePair = useCallback(
    (left: Senator, right: Senator, lCh: Chamber, rCh: Chamber) => {
      setLeftSenator(left);
      setRightSenator(right);
      setLeftChamber(lCh);
      setRightChamber(rCh);
      setPresetVersion((v) => v + 1);
      updateUrl(left, right, lCh, rCh);
    },
    [updateUrl]
  );
  const chosen =
    leftSenator && !rightSenator
      ? { member: leftSenator, chamber: leftChamber }
      : rightSenator && !leftSenator
        ? { member: rightSenator, chamber: rightChamber }
        : null;

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

  if (hydrating) {
    return (
      <>
        <Navbar />
        <main id="main-content" tabIndex={-1} className="pt-[var(--header-clearance)] pb-16 px-4">
          <div className="max-w-5xl mx-auto">
            {/* The masthead stays while a linked pair loads: the server's
                fallback sends it, and dropping it here made it vanish and
                come back. */}
            <PageMasthead className="mb-8" eyebrow={COMPARE_EYEBROW} title={COMPARE_TITLE}>
              {COMPARE_LEDE}
            </PageMasthead>
            <div className="panel p-8 text-center">
              <div className="font-mono text-xs text-ink-min tracking-widest pulse-subtle">
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
          <PageMasthead className="mb-8" eyebrow={COMPARE_EYEBROW} title={COMPARE_TITLE}>
            {COMPARE_LEDE}
          </PageMasthead>

          <ComparePresets chosen={chosen} onPair={handlePair} />

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-6">
            <SenatorSelector
              key={`left-${presetVersion}`}
              side="left"
              onSelect={handleLeft}
              selectedId={leftSenator?.id}
              initialChamber={leftChamber}
              initialState={leftSenator?.state}
            />
            <SenatorSelector
              key={`right-${presetVersion}`}
              side="right"
              onSelect={handleRight}
              selectedId={rightSenator?.id}
              initialChamber={rightChamber}
              initialState={rightSenator?.state}
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
    <Suspense
      fallback={
        <PageFallback
          eyebrow={COMPARE_EYEBROW}
          title={COMPARE_TITLE}
          lede={COMPARE_LEDE}
          mastheadClassName="mb-8"
          rows={3}
        />
      }
    >
      <ComparePageInner />
    </Suspense>
  );
}
