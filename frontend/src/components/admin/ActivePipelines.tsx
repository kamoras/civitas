"use client";

import type { AdminPipelineStatus } from "@/lib/api";
import { PipelineProgressBar } from "./pipelineWidgets";

/**
 * Live progress for every pipeline that is running right now — shown above
 * the tabs, whichever sub-dashboard is open, because a run in flight is the
 * one thing an operator should never have to go looking for.
 */
export function ActivePipelines({
  pipelineStatus,
}: {
  pipelineStatus: AdminPipelineStatus | null;
}) {
  return (
    <>
      {/* Live pipeline progress — one card per pipeline type. Previously
            only ever checked pipelineStatus.isRunning (Senate), so House /
            Stock Trades / Supplementary never showed this view while
            actively running, only Senate did. */}
      {pipelineStatus?.isRunning && pipelineStatus.lastRun && (
        <div className="mb-6">
          <PipelineProgressBar
            title="SENATE PIPELINE ACTIVE"
            isRunning={pipelineStatus.isRunning}
            run={pipelineStatus.lastRun}
            etaConfig={{
              processed: pipelineStatus.lastRun.senatorsProcessed,
              total: pipelineStatus.lastRun.senatorsTotal,
              unitLabel: "senator",
            }}
            statsRow={
              <>
                <span>LLM: {pipelineStatus.lastRun.llmCalls}</span>
                <span>
                  Cache: {pipelineStatus.lastRun.cacheHits}H / {pipelineStatus.lastRun.cacheMisses}M
                </span>
                <span>Bills: {pipelineStatus.lastRun.billsClassified}</span>
                <span>
                  Senators: {pipelineStatus.lastRun.senatorsProcessed}/
                  {pipelineStatus.lastRun.senatorsTotal}
                  {pipelineStatus.lastRun.senatorsFailed > 0 && (
                    <span className="text-signal-magenta ml-1">
                      ({pipelineStatus.lastRun.senatorsFailed}F)
                    </span>
                  )}
                </span>
              </>
            }
          />
        </div>
      )}

      {pipelineStatus?.houseIsRunning && pipelineStatus.houseLastRun && (
        <div className="mb-6">
          <PipelineProgressBar
            title="HOUSE PIPELINE ACTIVE"
            isRunning={pipelineStatus.houseIsRunning}
            run={pipelineStatus.houseLastRun}
            etaConfig={{
              processed: pipelineStatus.houseLastRun.repsProcessed,
              total: pipelineStatus.houseLastRun.repsTotal,
              unitLabel: "rep",
            }}
            statsRow={
              <span>
                Reps: {pipelineStatus.houseLastRun.repsProcessed}/
                {pipelineStatus.houseLastRun.repsTotal}
                {pipelineStatus.houseLastRun.repsFailed > 0 && (
                  <span className="text-signal-magenta ml-1">
                    ({pipelineStatus.houseLastRun.repsFailed}F)
                  </span>
                )}
              </span>
            }
          />
        </div>
      )}

      {pipelineStatus?.stockTradesIsRunning && pipelineStatus.stockTradesLastRun && (
        <div className="mb-6">
          <PipelineProgressBar
            title="STOCK TRADES PIPELINE ACTIVE"
            isRunning={pipelineStatus.stockTradesIsRunning}
            run={pipelineStatus.stockTradesLastRun}
            showPhaseBreadcrumb={false}
            statsRow={
              <span>
                Trades: {pipelineStatus.stockTradesLastRun.houseTradesIngested}H /{" "}
                {pipelineStatus.stockTradesLastRun.senateTradesIngested}S /{" "}
                {pipelineStatus.stockTradesLastRun.presidentTradesIngested}P
              </span>
            }
          />
        </div>
      )}

      {pipelineStatus?.supplementaryIsRunning && pipelineStatus.supplementaryLastRun && (
        <div className="mb-6">
          <PipelineProgressBar
            title="SUPPLEMENTARY PIPELINE ACTIVE"
            isRunning={pipelineStatus.supplementaryIsRunning}
            run={pipelineStatus.supplementaryLastRun}
            showPhaseBreadcrumb={false}
            statsRow={
              <>
                <span>Docs: {pipelineStatus.supplementaryLastRun.exploreDocsIngested}</span>
                <span>
                  SCOTUS:{" "}
                  {pipelineStatus.supplementaryLastRun.justicesSkipped
                    ? "skipped"
                    : pipelineStatus.supplementaryLastRun.justicesScored}
                </span>
                <span>Presidents: {pipelineStatus.supplementaryLastRun.presidentsUpdated}</span>
              </>
            }
          />
        </div>
      )}

      {pipelineStatus?.electionIsRunning && pipelineStatus.electionLastRun && (
        <div className="mb-6">
          <PipelineProgressBar
            title="ELECTION PIPELINE ACTIVE"
            isRunning={pipelineStatus.electionIsRunning}
            run={pipelineStatus.electionLastRun}
            showPhaseBreadcrumb={false}
            statsRow={
              <>
                <span>Candidates: {pipelineStatus.electionLastRun.candidatesSynced}</span>
                <span>Financials: {pipelineStatus.electionLastRun.financialsRefreshed}</span>
                <span>Coverage: {pipelineStatus.electionLastRun.coverageItemsIngested}</span>
              </>
            }
          />
        </div>
      )}
    </>
  );
}
