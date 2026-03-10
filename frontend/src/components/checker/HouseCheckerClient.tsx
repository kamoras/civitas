"use client";

import { useEffect, useState, useCallback } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import StateSelector from "./StateSelector";
import SenatorCard from "./SenatorCard";
import GlitchText from "@/components/effects/GlitchText";
import { STATES } from "@/data/states";
import { fetchRepresentativesByState } from "@/lib/api";
import type { Senator } from "@/types/senator";

export default function HouseCheckerClient() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const selectedState = searchParams.get("state") ?? "";
  const targetRep = searchParams.get("representative") ?? "";

  const [reps, setReps] = useState<Senator[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  const [totalPages, setTotalPages] = useState(1);
  const [totalReps, setTotalReps] = useState(0);

  const stateName = STATES.find((s) => s.code === selectedState)?.name || selectedState;

  useEffect(() => {
    if (!selectedState) {
      setReps([]);
      setTotalReps(0);
      return;
    }
    setLoading(true);
    setError(null);
    fetchRepresentativesByState(selectedState, page, 10)
      .then((data) => {
        setReps(data.entries);
        setTotalPages(data.totalPages);
        setTotalReps(data.total);
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [selectedState, page]);

  useEffect(() => {
    setPage(1);
  }, [selectedState]);

  useEffect(() => {
    if (!loading && targetRep && reps.length > 0) {
      const el = document.getElementById(`senator-${targetRep}`);
      if (el) {
        el.scrollIntoView({ behavior: "smooth", block: "start" });
      }
    }
  }, [loading, targetRep, reps]);

  const handleSelect = useCallback((stateCode: string) => {
    const params = new URLSearchParams(searchParams.toString());
    if (stateCode) {
      params.set("state", stateCode);
    } else {
      params.delete("state");
    }
    router.replace(`?${params.toString()}`, { scroll: false });
  }, [router, searchParams]);

  return (
    <div>
      <StateSelector selectedState={selectedState} onSelect={handleSelect} />

      {loading && (
        <div className="mt-12 text-center">
          <div className="terminal-window max-w-md mx-auto p-6">
            <div className="text-neon-cyan animate-pulse text-lg">
              {">"} PULLING PUBLIC RECORDS...
            </div>
            <div className="text-matrix-green/40 text-sm mt-2 animate-pulse">
              LOADING FEC FILINGS...
            </div>
            <div className="mt-4 text-matrix-green/20">
              {"["}
              <span className="animate-pulse">████████░░░░░░░░</span>
              {"]"}
            </div>
          </div>
        </div>
      )}

      {error && (
        <div className="mt-12 text-center">
          <div className="terminal-window max-w-md mx-auto p-6">
            <div className="text-red-500 text-lg">{">"} CONNECTION ERROR</div>
            <div className="text-matrix-green/40 text-sm mt-2">{error}</div>
          </div>
        </div>
      )}

      {!loading && !error && reps.length > 0 && (
        <div className="mt-12">
          <div className="text-center mb-8">
            <GlitchText
              text={`${stateName.toUpperCase()} // ${totalReps} REPRESENTATIVES`}
              as="h2"
              className="font-pixel text-sm sm:text-lg text-neon-pink"
            />
            <p className="text-matrix-green/40 text-sm mt-2">
              Public campaign finance and voting data. Showing {(page - 1) * 10 + 1}–{Math.min(page * 10, totalReps)} of {totalReps}.
            </p>
          </div>

          <div className="grid grid-cols-1 xl:grid-cols-2 gap-6">
            {reps.map((rep) => (
              <SenatorCard key={rep.id} senator={rep} chamber="house" />
            ))}
          </div>

          {totalPages > 1 && (
            <nav className="flex items-center justify-center gap-2 mt-8" aria-label="Representative pagination">
              <button
                onClick={() => { setPage((p) => Math.max(1, p - 1)); window.scrollTo({ top: 0, behavior: "smooth" }); }}
                disabled={page <= 1}
                className="px-3 py-1.5 text-sm border border-matrix-green/30 text-matrix-green hover:bg-matrix-green/10 disabled:opacity-30 disabled:cursor-not-allowed transition-colors font-terminal"
                aria-label="Previous page"
              >
                ← PREV
              </button>
              <div className="flex gap-1 flex-wrap justify-center">
                {Array.from({ length: totalPages }, (_, i) => i + 1).map((p) => (
                  <button
                    key={p}
                    onClick={() => { setPage(p); window.scrollTo({ top: 0, behavior: "smooth" }); }}
                    aria-current={page === p ? "page" : undefined}
                    className={`w-8 h-8 text-sm font-terminal border transition-colors ${
                      page === p
                        ? "bg-matrix-green/20 border-matrix-green text-matrix-green"
                        : "border-white/10 text-white/40 hover:border-white/30 hover:text-white/70"
                    }`}
                  >
                    {p}
                  </button>
                ))}
              </div>
              <button
                onClick={() => { setPage((p) => Math.min(totalPages, p + 1)); window.scrollTo({ top: 0, behavior: "smooth" }); }}
                disabled={page >= totalPages}
                className="px-3 py-1.5 text-sm border border-matrix-green/30 text-matrix-green hover:bg-matrix-green/10 disabled:opacity-30 disabled:cursor-not-allowed transition-colors font-terminal"
                aria-label="Next page"
              >
                NEXT →
              </button>
            </nav>
          )}
        </div>
      )}

      {!loading && !error && selectedState && reps.length === 0 && (
        <div className="mt-12 text-center">
          <div className="terminal-window max-w-md mx-auto p-6">
            <div className="text-neon-yellow text-lg">{">"} NO DATA YET</div>
            <div className="text-matrix-green/40 text-sm mt-2">
              House representative data for {stateName} is still being processed.
              The pipeline will populate this data on its next run.
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
