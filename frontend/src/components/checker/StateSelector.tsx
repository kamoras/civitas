"use client";

import { STATES } from "@/data/states";

interface StateSelectorProps {
  selectedState: string;
  onSelect: (stateCode: string) => void;
}

export default function StateSelector({ selectedState, onSelect }: StateSelectorProps) {
  return (
    <div className="flex flex-col items-center gap-4">
      <label htmlFor="state-select" className="text-lg sm:text-xl text-matrix-green/80">
        {">"} SELECT YOUR STATE TO VIEW SENATOR FUNDING DATA.
      </label>
      <div className="relative">
        <select
          id="state-select"
          value={selectedState}
          onChange={(e) => onSelect(e.target.value)}
          autoComplete="address-level1"
          className="appearance-none bg-crt-black border-2 border-matrix-green text-matrix-green font-terminal text-xl px-6 py-3 pr-12 cursor-pointer focus:outline-none focus:border-neon-cyan focus:shadow-[0_0_15px_rgba(0,255,255,0.3)] transition-all"
        >
          <option value="">-- CHOOSE STATE --</option>
          {STATES.map((s) => (
            <option key={s.code} value={s.code}>
              {s.code} - {s.name.toUpperCase()}
            </option>
          ))}
        </select>
        <div className="absolute right-3 top-1/2 -translate-y-1/2 text-matrix-green pointer-events-none" aria-hidden="true">
          ▼
        </div>
      </div>
    </div>
  );
}
