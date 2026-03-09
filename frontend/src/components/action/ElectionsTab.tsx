"use client";

import { useEffect, useState, useMemo } from "react";
import Link from "next/link";
import {
  ComposableMap,
  Geographies,
  Geography,
} from "react-simple-maps";
import { fetchElectionInfo } from "@/lib/api";
import type { ElectionInfo, ElectionState, ElectionSenator } from "@/lib/api";

const GEO_URL = "https://cdn.jsdelivr.net/npm/us-atlas@3/states-10m.json";

const FIPS_TO_STATE: Record<string, string> = {
  "01": "AL", "02": "AK", "04": "AZ", "05": "AR", "06": "CA",
  "08": "CO", "09": "CT", "10": "DE", "11": "DC", "12": "FL",
  "13": "GA", "15": "HI", "16": "ID", "17": "IL", "18": "IN",
  "19": "IA", "20": "KS", "21": "KY", "22": "LA", "23": "ME",
  "24": "MD", "25": "MA", "26": "MI", "27": "MN", "28": "MS",
  "29": "MO", "30": "MT", "31": "NE", "32": "NV", "33": "NH",
  "34": "NJ", "35": "NM", "36": "NY", "37": "NC", "38": "ND",
  "39": "OH", "40": "OK", "41": "OR", "42": "PA", "44": "RI",
  "45": "SC", "46": "SD", "47": "TN", "48": "TX", "49": "UT",
  "50": "VT", "51": "VA", "53": "WA", "54": "WV", "55": "WI",
  "56": "WY",
};

const PARTY_COLORS: Record<string, string> = {
  D: "text-dem-blue",
  R: "text-rep-red",
  I: "text-ind-purple",
};

const PARTY_BORDER: Record<string, string> = {
  D: "border-dem-blue/30",
  R: "border-rep-red/30",
  I: "border-ind-purple/30",
};

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

function SenatorRow({ senator }: { senator: ElectionSenator }) {
  return (
    <Link
      href={`/scorecard?branch=senate&state=&senator=${senator.id}`}
      className={`flex items-center justify-between gap-3 p-3 border ${PARTY_BORDER[senator.party]} bg-matrix-dark-green/20 hover:border-neon-cyan/40 transition-all group`}
    >
      <div className="flex items-center gap-2 min-w-0">
        <span className={`font-pixel text-[10px] shrink-0 ${PARTY_COLORS[senator.party]}`}>
          [{senator.party}]
        </span>
        <span className="text-sm text-matrix-green/80 group-hover:text-matrix-green truncate">
          {senator.name}
        </span>
        {senator.upForElection && (
          <span className="text-[9px] font-pixel px-1.5 py-0.5 bg-neon-yellow/10 border border-neon-yellow/30 text-neon-yellow/80 shrink-0">
            UP IN 2026
          </span>
        )}
      </div>
      <div className="flex items-center gap-3 shrink-0">
        <span className="text-[10px] text-matrix-green/40">
          {senator.yearsInOffice}yr
        </span>
        <span className="text-sm font-pixel text-neon-cyan/70">
          {Math.round(senator.overallScore)}
        </span>
      </div>
    </Link>
  );
}

function StatePanel({
  stateData,
  onClose,
}: {
  stateData: ElectionState;
  onClose: () => void;
}) {
  return (
    <div
      className="terminal-window border-t-2 border-t-neon-cyan/50 p-5"
      role="region"
      aria-label={`${stateData.state} election details`}
    >
      <div className="flex items-center justify-between mb-4">
        <h3 className="font-pixel text-lg text-matrix-green">
          {stateData.state}
        </h3>
        <button
          onClick={onClose}
          className="font-pixel text-sm text-matrix-green/40 hover:text-matrix-green"
          aria-label="Close state detail panel"
        >
          [✕]
        </button>
      </div>

      <div className="flex gap-3 mb-4 flex-wrap">
        {stateData.hasSenateRace && (
          <span className="text-[10px] font-pixel px-2 py-1 border border-neon-yellow/30 text-neon-yellow/80 bg-neon-yellow/5">
            SENATE RACE
          </span>
        )}
        {stateData.hasHouseRace && (
          <span className="text-[10px] font-pixel px-2 py-1 border border-neon-pink/30 text-neon-pink/80 bg-neon-pink/5">
            {stateData.houseDistricts} HOUSE {stateData.houseDistricts === 1 ? "SEAT" : "SEATS"}
          </span>
        )}
      </div>

      {stateData.senators.length > 0 && (
        <div className="mb-4">
          <h4 className="font-pixel text-xs text-matrix-green/50 mb-2">CURRENT SENATORS</h4>
          <div className="space-y-2">
            {stateData.senators.map((s) => (
              <SenatorRow key={s.id} senator={s} />
            ))}
          </div>
        </div>
      )}

      {stateData.hasHouseRace && (
        <div>
          <h4 className="font-pixel text-xs text-matrix-green/50 mb-2">HOUSE RACES</h4>
          <div className="p-3 border border-neon-pink/20 bg-neon-pink/5">
            <div className="flex items-center justify-between">
              <span className="text-sm text-matrix-green/70">
                {stateData.houseDistricts === 1
                  ? "At-large district"
                  : `${stateData.houseDistricts} congressional districts`}
              </span>
              <span className="text-[10px] font-pixel text-neon-pink/60">
                ALL UP IN {new Date().getFullYear() % 2 === 0 ? new Date().getFullYear() : new Date().getFullYear() + 1}
              </span>
            </div>
            <p className="text-[11px] text-matrix-green/40 mt-2">
              All {stateData.houseDistricts === 1 ? "1 seat" : `${stateData.houseDistricts} seats`} in the U.S. House are elected every 2 years.
            </p>
          </div>
        </div>
      )}

      {stateData.senators.length === 0 && !stateData.hasHouseRace && (
        <p className="text-sm text-matrix-green/40">No election data available for this state.</p>
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
      data.states
        .filter((s) => s.hasHouseRace && !s.hasSenateRace)
        .map((s) => s.state)
    );
  }, [data]);

  const selectedData = selectedState ? stateMap[selectedState] : null;

  if (loading) {
    return (
      <div className="terminal-window max-w-md mx-auto p-6 text-center">
        <div className="text-neon-cyan animate-pulse text-lg">{">"} LOADING ELECTION DATA...</div>
      </div>
    );
  }

  if (!data) {
    return (
      <div className="terminal-window max-w-md mx-auto p-6 text-center">
        <div className="text-matrix-green/50">Election data unavailable.</div>
      </div>
    );
  }

  const el = data.nextElection;
  const countdown = formatCountdown(el.daysUntil);

  return (
    <div className="space-y-6">
      {/* Countdown header */}
      <div className="terminal-window border-t-2 border-t-neon-yellow/50 p-6 text-center">
        {el.isElectionDay ? (
          <div>
            <div className="font-pixel text-2xl sm:text-4xl text-neon-yellow animate-pulse mb-3">
              ELECTION DAY
            </div>
            <p className="text-matrix-green/60 text-sm">{el.type}</p>
          </div>
        ) : (
          <div>
            <div className="text-[10px] font-pixel text-matrix-green/40 mb-3">
              NEXT FEDERAL ELECTION
            </div>
            <div className="flex items-center justify-center gap-4 sm:gap-6 mb-4">
              {countdown.map((part, i) => (
                <div key={i} className="text-center">
                  <div className="font-pixel text-3xl sm:text-5xl text-neon-cyan">
                    {part.value}
                  </div>
                  <div className="text-[10px] font-pixel text-matrix-green/40 mt-1">
                    {part.unit}
                  </div>
                </div>
              ))}
            </div>
            <div className="text-sm text-matrix-green/60 mb-1">{el.type}</div>
            <div className="text-[10px] text-matrix-green/30 font-pixel">{el.date}</div>
          </div>
        )}

        <div className="flex justify-center gap-6 mt-5 pt-4 border-t border-matrix-green/10">
          <div className="text-center">
            <div className="font-pixel text-xl text-neon-yellow">{data.senateSeatsUp}</div>
            <div className="text-[10px] font-pixel text-matrix-green/40">SENATE SEATS</div>
          </div>
          <div className="text-center">
            <div className="font-pixel text-xl text-neon-pink">{data.houseSeatsUp}</div>
            <div className="text-[10px] font-pixel text-matrix-green/40">HOUSE SEATS</div>
          </div>
          {el.year % 4 === 0 && (
            <div className="text-center">
              <div className="font-pixel text-xl text-neon-cyan">1</div>
              <div className="text-[10px] font-pixel text-matrix-green/40">PRESIDENCY</div>
            </div>
          )}
        </div>
      </div>

      {/* Interactive US map */}
      <div className="terminal-window p-4">
        <div className="flex items-center justify-between mb-3 flex-wrap gap-2">
          <h3 className="font-pixel text-xs text-matrix-green/50">
            {">"} SELECT A STATE
          </h3>
          <div className="flex items-center gap-3 flex-wrap">
            <span className="flex items-center gap-1.5 text-[10px] text-matrix-green/40">
              <span className="w-3 h-2 bg-neon-yellow/40 inline-block" /> SENATE + HOUSE
            </span>
            <span className="flex items-center gap-1.5 text-[10px] text-matrix-green/40">
              <span className="w-3 h-2 inline-block" style={{ background: "rgba(255, 100, 200, 0.3)" }} /> HOUSE ONLY
            </span>
          </div>
        </div>

        <ComposableMap
          projection="geoAlbersUsa"
          projectionConfig={{ scale: 1000 }}
          width={980}
          height={600}
          style={{ width: "100%", height: "auto" }}
        >
          <Geographies geography={GEO_URL}>
            {({ geographies }) =>
              geographies.map((geo) => {
                const fips = geo.id as string;
                const stateCode = FIPS_TO_STATE[fips];
                if (!stateCode) return null;
                const hasSenate = senateRaceStates.has(stateCode);
                const hasHouseOnly = houseOnlyStates.has(stateCode);
                const isSelected = selectedState === stateCode;

                const defaultFill = isSelected
                  ? "#00ffff"
                  : hasSenate
                    ? "rgba(255, 255, 0, 0.35)"
                    : hasHouseOnly
                      ? "rgba(255, 100, 200, 0.25)"
                      : "rgba(0, 255, 65, 0.15)";
                const hoverFill = isSelected
                  ? "#00ffff"
                  : hasSenate
                    ? "rgba(255, 255, 0, 0.55)"
                    : hasHouseOnly
                      ? "rgba(255, 100, 200, 0.45)"
                      : "rgba(0, 255, 65, 0.35)";

                return (
                  <Geography
                    key={geo.rsmKey}
                    geography={geo}
                    onClick={() => setSelectedState(isSelected ? null : stateCode)}
                    style={{
                      default: {
                        fill: defaultFill,
                        stroke: "#0a1a0a",
                        strokeWidth: 0.5,
                        outline: "none",
                        cursor: "pointer",
                      },
                      hover: {
                        fill: hoverFill,
                        stroke: "#00ff41",
                        strokeWidth: 1,
                        outline: "none",
                        cursor: "pointer",
                      },
                      pressed: {
                        fill: "#00ffff",
                        stroke: "#00ff41",
                        strokeWidth: 1,
                        outline: "none",
                      },
                    }}
                  />
                );
              })
            }
          </Geographies>
        </ComposableMap>
      </div>

      <details className="terminal-window p-4 mt-4">
        <summary className="font-pixel text-xs text-matrix-green/60 hover:text-matrix-green cursor-pointer">
          List all states (keyboard accessible)
        </summary>
        <div className="grid grid-cols-5 sm:grid-cols-8 md:grid-cols-10 gap-2 mt-3">
          {Object.values(FIPS_TO_STATE).map((abbr) => (
              <button
                key={abbr}
                onClick={() => setSelectedState(selectedState === abbr ? null : abbr)}
                className={`font-pixel text-[10px] py-1.5 px-2 border rounded transition-colors ${
                  selectedState === abbr
                    ? "border-neon-cyan bg-neon-cyan/20 text-neon-cyan"
                    : "border-matrix-green/20 text-matrix-green/70 hover:border-matrix-green/40"
                }`}
              >
                {abbr}
              </button>
            ))}
        </div>
      </details>

      {/* State detail panel */}
      {selectedData && (
        <StatePanel
          stateData={selectedData}
          onClose={() => setSelectedState(null)}
        />
      )}

      {!selectedData && (
        <div className="terminal-window p-4 text-center">
          <p className="text-matrix-green/40 text-sm">
            Click a state on the map to see its races and representatives.
          </p>
        </div>
      )}
    </div>
  );
}
