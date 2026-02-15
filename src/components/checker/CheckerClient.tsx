"use client";

import { useState } from "react";
import { Senator } from "@/types/senator";
import StateSelector from "./StateSelector";
import SenatorCard from "./SenatorCard";
import GlitchText from "@/components/effects/GlitchText";
import { STATES } from "@/data/states";

interface CheckerClientProps {
  senators: Senator[];
}

export default function CheckerClient({ senators }: CheckerClientProps) {
  const [selectedState, setSelectedState] = useState("");
  const [loading, setLoading] = useState(false);
  const [showResults, setShowResults] = useState(false);

  const stateSenators = senators.filter((s) => s.state === selectedState);
  const stateName = STATES.find((s) => s.code === selectedState)?.name || selectedState;

  const handleSelect = (stateCode: string) => {
    if (stateCode === "") {
      setSelectedState("");
      setShowResults(false);
      return;
    }
    setSelectedState(stateCode);
    setLoading(true);
    setShowResults(false);

    setTimeout(() => {
      setLoading(false);
      setShowResults(true);
    }, 1200);
  };

  return (
    <div>
      <StateSelector selectedState={selectedState} onSelect={handleSelect} />

      {/* Loading state */}
      {loading && (
        <div className="mt-12 text-center">
          <div className="terminal-window max-w-md mx-auto p-6">
            <div className="text-neon-cyan animate-pulse text-lg">
              {">"} PULLING PUBLIC RECORDS...
            </div>
            <div className="text-matrix-green/40 text-sm mt-2 animate-pulse">
              LOADING FEC FILINGS...
            </div>
            <div className="text-matrix-green/30 text-sm mt-1 animate-pulse">
              MATCHING LOBBYING DATA...
            </div>
            <div className="mt-4 text-matrix-green/20">
              {"["}
              <span className="animate-pulse">████████░░░░░░░░</span>
              {"]"}
            </div>
          </div>
        </div>
      )}

      {/* Results */}
      {showResults && stateSenators.length > 0 && (
        <div className="mt-12">
          <div className="text-center mb-8">
            <GlitchText
              text={`${stateName.toUpperCase()} // ${stateSenators.length} SENATORS`}
              as="h2"
              className="font-pixel text-sm sm:text-lg text-neon-pink"
            />
            <p className="text-matrix-green/40 text-sm mt-2">
              Public campaign finance and voting data.
            </p>
          </div>

          <div className="grid grid-cols-1 xl:grid-cols-2 gap-6">
            {stateSenators.map((senator) => (
              <SenatorCard key={senator.id} senator={senator} />
            ))}
          </div>
        </div>
      )}

      {showResults && stateSenators.length === 0 && (
        <div className="mt-12 text-center">
          <div className="terminal-window max-w-md mx-auto p-6">
            <div className="text-red-500 text-lg">{">"} NO DATA FOUND</div>
            <div className="text-matrix-green/40 text-sm mt-2">
              No senator records found for {stateName}. We may not have data for this state yet.
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
