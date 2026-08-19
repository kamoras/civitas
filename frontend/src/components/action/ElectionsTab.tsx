"use client";

import { useEffect, useState, useMemo } from "react";
import Link from "next/link";
import { fetchElectionInfo } from "@/lib/api";
import type { ElectionInfo, ElectionState, ElectionSenator } from "@/lib/api";
import { PARTY_COLORS, PARTY_BORDER } from "@/lib/partyStyles";
import RaceMap, { FIPS_TO_STATE } from "@/components/elections/RaceMap";
import { BOXED_CONTROL } from "@/lib/controlStyles";

function formatCountdown(days: number): { value: string; unit: string }[] {
  if (days <= 0) return [{ value: "TODAY", unit: "" }];
  const d = days;
  const months = Math.floor(d / 30);
  const weeks = Math.floor((d % 30) / 7);
  const remaining = d % 7;
  const parts: { value: string; unit: string }[] = [];
  if (months > 0) parts.push({ value: String(months), unit: months === 1 ? "MONTH" : "MONTHS" });
  if (weeks > 0) parts.push({ value: String(weeks), unit: weeks === 1 ? "WEEK" : "WEEKS" });
  if (remaining > 0 || parts.length === 0)
    parts.push({ value: String(remaining), unit: remaining === 1 ? "DAY" : "DAYS" });
  return parts;
}

/** The next even (federal-election) year: this year if even, else next year. */
function nextElectionYear(): number {
  const y = new Date().getFullYear();
  return y % 2 === 0 ? y : y + 1;
}

function SenatorRow({ senator }: { senator: ElectionSenator }) {
  return (
    <Link
      href={`/politicians/${senator.id}`}
      className={`flex items-center justify-between gap-3 p-3 border ${PARTY_BORDER[senator.party]} bg-white/[0.03] hover:border-signal-cyan/40 transition-all group`}
    >
      <div className="flex items-center gap-2 min-w-0">
        <span className={`font-mono text-xs shrink-0 ${PARTY_COLORS[senator.party]}`}>
          [{senator.party}]
        </span>
        <span className="text-sm text-ink group-hover:text-phos truncate">{senator.name}</span>
        {senator.upForElection && (
          <span className="text-xs font-mono px-1.5 py-0.5 bg-signal-amber/10 border border-signal-amber/40 text-signal-amber shrink-0">
            UP IN {nextElectionYear()}
          </span>
        )}
      </div>
      <div className="flex items-center gap-3 shrink-0">
        <span className="text-xs text-ink-min">{senator.yearsInOffice}yr</span>
        <span className="text-sm font-mono text-signal-cyan">
          {Math.round(senator.overallScore)}
        </span>
      </div>
    </Link>
  );
}

function StatePanel({ stateData, onClose }: { stateData: ElectionState; onClose: () => void }) {
  return (
    <div
      className="panel border-t-2 border-t-signal-cyan p-5"
      role="region"
      aria-label={`${stateData.state} election details`}
    >
      <div className="flex items-center justify-between mb-4">
        <h2 className="font-display font-semibold text-lg text-ink-hi">{stateData.state}</h2>
        <button
          onClick={onClose}
          className="font-mono text-sm text-ink-min hover:text-phos"
          aria-label="Close state detail panel"
        >
          [✕]
        </button>
      </div>

      <div className="flex gap-3 mb-4 flex-wrap">
        {stateData.hasSenateRace && (
          <span className="text-xs font-mono px-2 py-1 border border-signal-amber/40 text-signal-amber bg-signal-amber/10">
            SENATE RACE
          </span>
        )}
        {stateData.hasHouseRace && (
          <span className="text-xs font-mono px-2 py-1 border border-signal-magenta/40 text-ink-lo bg-signal-magenta/10">
            {stateData.houseDistricts} HOUSE {stateData.houseDistricts === 1 ? "SEAT" : "SEATS"}
          </span>
        )}
      </div>

      {stateData.senators.length > 0 && (
        <div className="mb-4">
          <h4 className="font-mono text-xs text-ink-lo mb-2">CURRENT SENATORS</h4>
          <div className="space-y-2">
            {stateData.senators.map((s) => (
              <SenatorRow key={s.id} senator={s} />
            ))}
          </div>
        </div>
      )}

      {stateData.hasHouseRace && (
        <div>
          <h4 className="font-mono text-xs text-ink-lo mb-2">HOUSE RACES</h4>
          <div className="p-3 border border-signal-magenta/40 bg-signal-magenta/10">
            <div className="flex items-center justify-between">
              <span className="text-sm text-ink">
                {stateData.houseDistricts === 1
                  ? "At-large district"
                  : `${stateData.houseDistricts} congressional districts`}
              </span>
              <span className="text-xs font-mono text-ink-lo">ALL UP IN {nextElectionYear()}</span>
            </div>
            <p className="text-xs text-ink-min mt-2">
              All {stateData.houseDistricts === 1 ? "1 seat" : `${stateData.houseDistricts} seats`}{" "}
              in the U.S. House are elected every 2 years.
            </p>
          </div>
        </div>
      )}

      {stateData.senators.length === 0 && !stateData.hasHouseRace && (
        <p className="text-base text-ink-min">No election data available for this state.</p>
      )}

      {(stateData.hasSenateRace || stateData.hasHouseRace) && (
        <Link
          href={`/elections?state=${stateData.state}`}
          className="inline-block mt-4 text-xs font-mono text-signal-cyan hover:text-phos"
        >
          View full race coverage →
        </Link>
      )}
    </div>
  );
}

export default function ElectionsTab() {
  const [data, setData] = useState<ElectionInfo | null>(null);
  const [loading, setLoading] = useState(true);
  const [selectedState, setSelectedState] = useState<string | null>(null);

  useEffect(() => {
    fetchElectionInfo()
      .then(setData)
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  const stateMap = useMemo(() => {
    if (!data) return {};
    const m: Record<string, ElectionState> = {};
    for (const s of data.states) m[s.state] = s;
    return m;
  }, [data]);

  const senateRaceStates = useMemo(() => {
    if (!data) return new Set<string>();
    return new Set(data.states.filter((s) => s.hasSenateRace).map((s) => s.state));
  }, [data]);

  const houseOnlyStates = useMemo(() => {
    if (!data) return new Set<string>();
    return new Set(
      data.states.filter((s) => s.hasHouseRace && !s.hasSenateRace).map((s) => s.state)
    );
  }, [data]);

  const selectedData = selectedState ? stateMap[selectedState] : null;

  if (loading) {
    return (
      <div className="panel max-w-md mx-auto p-6 text-center">
        <div className="text-signal-cyan animate-pulse text-lg">{">"} LOADING ELECTION DATA...</div>
      </div>
    );
  }

  // The countdown is the whole point of the header, so a payload without one
  // is "unavailable", not a header reading "NaN DAYS". This tab is the first
  // thing to go quiet when the elections pipeline has not run yet.
  if (!data?.nextElection) {
    return (
      <div className="panel max-w-md mx-auto p-6 text-center">
        <div className="text-ink-lo">Election data unavailable.</div>
      </div>
    );
  }

  const el = data.nextElection;
  const countdown = formatCountdown(el.daysUntil);

  return (
    <div className="space-y-6">
      {/* Countdown header */}
      <div className="panel border-t-2 border-t-signal-amber p-6 text-center">
        {el.isElectionDay ? (
          <div>
            <div className="font-display font-semibold text-2xl sm:text-4xl text-signal-amber animate-pulse mb-3">
              ELECTION DAY
            </div>
            <p className="text-ink-lo text-base">{el.type}</p>
          </div>
        ) : (
          <div>
            <div className="text-xs font-mono text-ink-min mb-3">NEXT FEDERAL ELECTION</div>
            <div className="flex items-center justify-center gap-4 sm:gap-6 mb-4">
              {countdown.map((part, i) => (
                <div key={i} className="text-center">
                  <div className="font-display font-semibold text-3xl sm:text-5xl text-signal-cyan">
                    {part.value}
                  </div>
                  <div className="text-xs font-mono text-ink-min mt-1">{part.unit}</div>
                </div>
              ))}
            </div>
            <div className="text-sm text-ink-lo mb-1">{el.type}</div>
            <div className="text-xs text-ink-min font-mono">{el.date}</div>
          </div>
        )}

        <div className="flex justify-center gap-6 mt-5 pt-4 border-t border-white/[0.07]">
          <div className="text-center">
            <div className="font-display font-semibold text-xl text-signal-amber">
              {data.senateSeatsUp}
            </div>
            <div className="text-xs font-mono text-ink-min">SENATE SEATS</div>
          </div>
          <div className="text-center">
            <div className="font-display font-semibold text-xl text-signal-magenta">
              {data.houseSeatsUp}
            </div>
            <div className="text-xs font-mono text-ink-min">HOUSE SEATS</div>
          </div>
          {el.year % 4 === 0 && (
            <div className="text-center">
              <div className="font-display font-semibold text-xl text-signal-cyan">1</div>
              <div className="text-xs font-mono text-ink-min">PRESIDENCY</div>
            </div>
          )}
        </div>
      </div>

      {/* Interactive US map */}
      <div className="panel p-4">
        <div className="flex items-center justify-between mb-3 flex-wrap gap-2">
          <h2 className="font-mono text-xs text-ink-lo">{">"} SELECT A STATE</h2>
          <div className="flex items-center gap-3 flex-wrap">
            <span className="flex items-center gap-1.5 text-xs text-ink-min">
              <span className="w-3 h-2 bg-signal-amber/10 inline-block" /> SENATE + HOUSE
            </span>
            <span className="flex items-center gap-1.5 text-xs text-ink-min">
              <span className="w-3 h-2 inline-block bg-signal-magenta/10" /> HOUSE ONLY
            </span>
          </div>
        </div>

        <RaceMap
          selectedState={selectedState}
          onStateClick={(stateCode) =>
            setSelectedState(selectedState === stateCode ? null : stateCode)
          }
          getFillColor={(stateCode) =>
            senateRaceStates.has(stateCode)
              ? "rgba(255, 255, 0, 0.35)"
              : houseOnlyStates.has(stateCode)
                ? "rgba(255, 100, 200, 0.25)"
                : "rgba(0, 255, 65, 0.15)"
          }
          getHoverFillColor={(stateCode) =>
            senateRaceStates.has(stateCode)
              ? "rgba(255, 255, 0, 0.55)"
              : houseOnlyStates.has(stateCode)
                ? "rgba(255, 100, 200, 0.45)"
                : "rgba(0, 255, 65, 0.35)"
          }
        />
      </div>

      <details className="panel p-4 mt-4">
        <summary className="font-mono text-xs text-ink-lo hover:text-phos cursor-pointer">
          List all states (keyboard accessible)
        </summary>
        <div className="grid grid-cols-5 sm:grid-cols-8 md:grid-cols-10 gap-2 mt-3">
          {Object.values(FIPS_TO_STATE).map((abbr) => (
            <button
              key={abbr}
              onClick={() => setSelectedState(selectedState === abbr ? null : abbr)}
              className={`font-mono text-xs py-1.5 px-2 border  transition-colors ${
                selectedState === abbr
                  ? BOXED_CONTROL.selected
                  : BOXED_CONTROL.unselected
              }`}
            >
              {abbr}
            </button>
          ))}
        </div>
      </details>

      {/* State detail panel */}
      {selectedData && (
        <StatePanel stateData={selectedData} onClose={() => setSelectedState(null)} />
      )}

      {!selectedData && (
        <div className="panel p-4 text-center">
          <p className="text-ink-min text-base">
            Click a state on the map to see its races and representatives.
          </p>
        </div>
      )}
    </div>
  );
}
