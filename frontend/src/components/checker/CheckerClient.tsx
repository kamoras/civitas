"use client";

import { useEffect } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { useSenatorsByState } from "@/hooks/useSenators";
import StateSelector from "./StateSelector";
import SenatorCard from "./SenatorCard";
import GlitchText from "@/components/effects/GlitchText";
import { STATES } from "@/data/states";

export default function CheckerClient() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const selectedState = searchParams.get("state") ?? "";
  const targetSenator = searchParams.get("senator") ?? "";

  const { senators: stateSenators, loading, error } = useSenatorsByState(selectedState);
  const stateName = STATES.find((s) => s.code === selectedState)?.name || selectedState;

  useEffect(() => {
    if (!loading && targetSenator && stateSenators.length > 0) {
      const el = document.getElementById(`senator-${targetSenator}`);
      if (el) {
        const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
        el.scrollIntoView({ behavior: reducedMotion ? "auto" : "smooth", block: "start" });
      }
    }
  }, [loading, targetSenator, stateSenators]);

  const handleSelect = (stateCode: string) => {
    const params = new URLSearchParams(searchParams.toString());
    if (stateCode) {
      params.set("state", stateCode);
    } else {
      params.delete("state");
    }
    router.replace(`?${params.toString()}`, { scroll: false });
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
            <div className="text-matrix-green/50 text-sm mt-1 animate-pulse">
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

      {/* Error state */}
      {error && (
        <div className="mt-12 text-center">
          <div className="terminal-window max-w-md mx-auto p-6">
            <div className="text-red-500 text-lg">{">"} CONNECTION ERROR</div>
            <div className="text-matrix-green/40 text-sm mt-2">{error}</div>
          </div>
        </div>
      )}

      {/* Results */}
      {!loading && !error && stateSenators.length > 0 && (
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

      {!loading && !error && selectedState && stateSenators.length === 0 && (
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
