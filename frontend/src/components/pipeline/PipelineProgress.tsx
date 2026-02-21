"use client";

import { useEffect, useState } from "react";
import { fetchPipelineStatus, PipelineStatus } from "@/lib/api";

const PHASE_LABELS: Record<string, string> = {
  fetch: "FETCHING DATA",
  transform: "TRANSFORMING",
  analyze: "ANALYZING",
  finalize: "FINALIZING",
};

const POLL_INTERVAL_MS = 5000;

export default function PipelineProgress() {
  const [status, setStatus] = useState<PipelineStatus | null>(null);

  useEffect(() => {
    let active = true;

    async function poll() {
      const data = await fetchPipelineStatus();
      if (active) setStatus(data);
    }

    poll();
    const id = setInterval(poll, POLL_INTERVAL_MS);
    return () => {
      active = false;
      clearInterval(id);
    };
  }, []);

  if (!status?.isRunning || !status.lastRun) return null;

  const run = status.lastRun;
  const phase = run.currentPhase ?? "fetch";
  const phaseLabel = PHASE_LABELS[phase] ?? phase.toUpperCase();

  const total = run.senatorsTotal ?? 0;
  const processed = run.senatorsProcessed ?? 0;
  const isAnalyzePhase = phase === "analyze" && total > 0;
  const pct = isAnalyzePhase ? Math.round((processed / total) * 100) : null;

  return (
    <div className="fixed bottom-0 left-0 right-0 z-50 bg-crt-black/95 border-t border-matrix-green/40 px-4 py-3">
      <div className="max-w-7xl mx-auto flex flex-col gap-1.5">
        <div className="flex items-center justify-between text-sm font-terminal">
          <span className="text-matrix-green animate-pulse">
            ▶ PIPELINE RUNNING — {phaseLabel}
          </span>
          {isAnalyzePhase && (
            <span className="text-matrix-green/70 text-xs">
              {processed} / {total} SENATORS
            </span>
          )}
        </div>

        <div className="w-full h-2 bg-matrix-green/10 border border-matrix-green/20 rounded-sm overflow-hidden">
          {isAnalyzePhase ? (
            // Determinate bar during analyze phase
            <div
              className="h-full bg-matrix-green transition-all duration-700 ease-out"
              style={{ width: `${pct}%` }}
            />
          ) : (
            // Indeterminate scanner for fetch/transform/finalize
            <div className="h-full w-full relative overflow-hidden">
              <div className="absolute inset-0 bg-matrix-green/20" />
              <div
                className="absolute inset-y-0 w-1/3 bg-matrix-green/80"
                style={{ animation: "pipeline-scan 1.6s ease-in-out infinite" }}
              />
            </div>
          )}
        </div>
      </div>

      <style>{`
        @keyframes pipeline-scan {
          0%   { left: -33%; }
          100% { left: 100%; }
        }
      `}</style>
    </div>
  );
}
