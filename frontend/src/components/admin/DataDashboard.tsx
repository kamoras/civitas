"use client";

import type { AdminDashboard } from "@/lib/api";
import TerminalTitlebar from "@/components/TerminalTitlebar";
import { formatBytes, formatTime } from "./format";

// --- Data Inventory ---
const INVENTORY_SECTIONS: { label: string; keys: { key: string; label: string }[] }[] = [
  {
    label: "SENATE",
    keys: [
      { key: "senators", label: "SENATORS" },
      { key: "senatorDonors", label: "DONORS" },
      { key: "senatorIndustryDonations", label: "INDUSTRY $" },
      { key: "senatorVotes", label: "VOTES" },
      { key: "senatorLobbyingMatches", label: "LOBBY MATCHES" },
      { key: "senatorPromises", label: "PROMISES" },
      { key: "senatorBills", label: "BILLS" },
    ],
  },
  {
    label: "HOUSE",
    keys: [
      { key: "representatives", label: "REPS" },
      { key: "repDonors", label: "DONORS" },
      { key: "repIndustryDonations", label: "INDUSTRY $" },
      { key: "repVotes", label: "VOTES" },
      { key: "repLobbyingMatches", label: "LOBBY MATCHES" },
      { key: "repPromises", label: "PROMISES" },
      { key: "repBills", label: "BILLS" },
    ],
  },
  {
    label: "EXECUTIVE & JUDICIARY",
    keys: [
      { key: "presidents", label: "PRESIDENTS" },
      { key: "justices", label: "JUSTICES" },
      { key: "justiceVotes", label: "JUSTICE VOTES" },
    ],
  },
  {
    label: "ACTION CENTER",
    keys: [
      { key: "actionIssues", label: "ISSUES" },
      { key: "nationalMonitors", label: "MONITORS" },
      { key: "monitorUpdates", label: "MONITOR UPDATES" },
      { key: "timelineEntries", label: "TIMELINE ENTRIES" },
      { key: "exploreDocuments", label: "EXPLORE DOCS" },
    ],
  },
  {
    label: "SYSTEM",
    keys: [
      { key: "scoreSnapshots", label: "SCORE SNAPSHOTS" },
      { key: "learnedClassifications", label: "LEARNED CLASSES" },
      { key: "pipelineRuns", label: "PIPELINE RUNS" },
      { key: "apiCacheEntries", label: "API CACHE" },
      { key: "analysisCacheEntries", label: "ANALYSIS CACHE" },
    ],
  },
];

function DataInventory({ data }: { data: Record<string, number> }) {
  const total = Object.values(data).reduce((s, n) => s + n, 0);

  return (
    <div className="p-4 space-y-4">
      <div className="flex items-center justify-between mb-1">
        <span className="font-mono text-xs text-ink-min tracking-widest">TOTAL RECORDS</span>
        <span className="font-mono text-sm text-ink-hi">{total.toLocaleString()}</span>
      </div>
      {INVENTORY_SECTIONS.map((section) => {
        const sectionTotal = section.keys.reduce((s, { key }) => s + (data[key] ?? 0), 0);
        if (sectionTotal === 0 && section.label !== "SYSTEM") return null;
        return (
          <div key={section.label}>
            <div className="flex items-center gap-2 mb-2">
              <span className="font-mono text-xs text-ind-purple tracking-widest">
                {section.label}
              </span>
              <span className="text-xs font-mono text-ink-min">
                {sectionTotal.toLocaleString()}
              </span>
              <div className="flex-1 border-t border-white/[0.07]" />
            </div>
            <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-2">
              {section.keys.map(({ key, label }) => (
                <div key={key} className="border border-white/[0.07] p-2.5 text-center">
                  <div className="text-base font-mono text-ink-hi">
                    {(data[key] ?? 0).toLocaleString()}
                  </div>
                  <div className="text-xs font-mono text-ink-lo tracking-wider mt-0.5">{label}</div>
                </div>
              ))}
            </div>
          </div>
        );
      })}
    </div>
  );
}

export function DataDashboard({ d }: { d: AdminDashboard | null }) {
  return (
    <div className="space-y-6">
      <div className="panel">
        <TerminalTitlebar title="Data inventory" />
        {d ? (
          <DataInventory data={d.data} />
        ) : (
          <p className="p-4 text-xs font-mono text-ink-min">Loading…</p>
        )}
      </div>

      {d?.system.vectorDb && (
        <div className="panel">
          <TerminalTitlebar title="Vector index" />
          <div className="p-4 space-y-4">
            {d.system.vectorDb.status !== "ok" ? (
              <p className="text-signal-magenta text-xs font-mono">
                VECTOR DB UNAVAILABLE: {d.system.vectorDb.error}
              </p>
            ) : (
              <>
                {/* Embedding Model */}
                <div>
                  <h3 className="text-xs font-mono text-ink-lo tracking-wider mb-2">
                    EMBEDDING MODELS
                  </h3>
                  <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                    <div className="border border-white/[0.07] p-3">
                      <div className="text-xs font-mono text-ink-min mb-1">
                        CLASSIFICATION MODEL
                      </div>
                      <div className="text-xs font-mono text-signal-cyan break-all">
                        {d.system.vectorDb.embeddingModel}
                      </div>
                      <div className="text-xs font-mono text-ink-lo mt-1">
                        v: {d.system.vectorDb.embeddingModelVersion}
                      </div>
                    </div>
                    <div className="border border-white/[0.07] p-3">
                      <div className="text-xs font-mono text-ink-min mb-1">SEARCH INDEX MODEL</div>
                      <div className="text-sm font-mono text-ink-hi">
                        {d.system.vectorDb.indexModelVersion || "rebuilding…"}
                      </div>
                    </div>
                    <div className="border border-white/[0.07] p-3">
                      <div className="text-xs font-mono text-ink-min mb-1">DIMENSIONS</div>
                      <div className="text-sm font-mono text-ink-hi">
                        {d.system.vectorDb.embeddingDimensions}
                      </div>
                    </div>
                  </div>
                </div>

                {/* Collections */}
                <div>
                  <h3 className="text-xs font-mono text-ink-lo tracking-wider mb-2">
                    COLLECTIONS ({d.system.vectorDb.collections?.length ?? 0})
                  </h3>
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                    {d.system.vectorDb.collections?.map((col) => {
                      const pct = d.system.vectorDb!.totalVectors
                        ? Math.round((col.count / d.system.vectorDb!.totalVectors!) * 100)
                        : 0;
                      return (
                        <div key={col.name} className="border border-white/[0.07] p-3">
                          <div className="flex justify-between items-center mb-2">
                            <span className="text-xs font-mono text-ink-hi">{col.name}</span>
                            <span className="text-xs font-mono text-signal-cyan">
                              {col.count.toLocaleString()}
                            </span>
                          </div>
                          <div
                            className="w-full h-1.5 bg-white/[0.03] overflow-hidden mb-2"
                            role="progressbar"
                            aria-valuenow={pct}
                            aria-valuemin={0}
                            aria-valuemax={100}
                            aria-label={`${col.name} vector count`}
                          >
                            <div
                              className="h-full bg-phos transition-all"
                              style={{ width: `${Math.max(pct, 2)}%` }}
                            />
                          </div>
                          <div className="text-xs font-mono text-ink-min">
                            {pct}% of total vectors
                          </div>
                          {col.sampleMetadataKeys && col.sampleMetadataKeys.length > 0 && (
                            <div className="mt-1 text-xs font-mono text-ink-min">
                              fields: {col.sampleMetadataKeys.join(", ")}
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                </div>

                {/* Storage Summary */}
                <div>
                  <h3 className="text-xs font-mono text-ink-lo tracking-wider mb-2">STORAGE</h3>
                  <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                    <div className="border border-white/[0.07] p-3 text-center">
                      <div className="text-lg font-mono text-ink-hi">
                        {(d.system.vectorDb.totalVectors ?? 0).toLocaleString()}
                      </div>
                      <div className="text-xs font-mono text-ink-lo">TOTAL VECTORS</div>
                    </div>
                    <div className="border border-white/[0.07] p-3 text-center">
                      <div className="text-lg font-mono text-ink-hi">
                        {formatBytes(d.system.vectorDb.sizeBytes ?? 0)}
                      </div>
                      <div className="text-xs font-mono text-ink-lo">DISK SIZE</div>
                    </div>
                    <div className="border border-white/[0.07] p-3 text-center">
                      <div className="text-lg font-mono text-ink-hi">
                        {d.system.vectorDb.collections?.length ?? 0}
                      </div>
                      <div className="text-xs font-mono text-ink-lo">COLLECTIONS</div>
                    </div>
                    <div className="border border-white/[0.07] p-3 text-center">
                      <div className="text-lg font-mono text-ink-hi">
                        {d.system.vectorDb.totalVectors && d.system.vectorDb.sizeBytes
                          ? `${Math.round(d.system.vectorDb.sizeBytes / d.system.vectorDb.totalVectors)} B`
                          : "—"}
                      </div>
                      <div className="text-xs font-mono text-ink-lo">AVG PER VECTOR</div>
                    </div>
                  </div>
                </div>

                {/* Learning Store */}
                {d.system.vectorDb.learningStore && !d.system.vectorDb.learningStore.error && (
                  <div>
                    <h3 className="text-xs font-mono text-ink-lo tracking-wider mb-2">
                      LEARNING STORE
                    </h3>
                    <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-3">
                      <div className="border border-white/[0.07] p-3 text-center">
                        <div className="text-lg font-mono text-ink-hi">
                          {d.system.vectorDb.learningStore.totalEntries.toLocaleString()}
                        </div>
                        <div className="text-xs font-mono text-ink-lo">CLASSIFICATIONS</div>
                      </div>
                      <div className="border border-white/[0.07] p-3 text-center">
                        <div className="text-lg font-mono text-ink-hi">
                          {d.system.vectorDb.learningStore.avgConfidence != null
                            ? `${(d.system.vectorDb.learningStore.avgConfidence * 100).toFixed(1)}%`
                            : "—"}
                        </div>
                        <div className="text-xs font-mono text-ink-lo">AVG CONFIDENCE</div>
                      </div>
                      <div className="border border-white/[0.07] p-3 text-center">
                        <div className="text-lg font-mono text-ink-hi">
                          {Object.keys(d.system.vectorDb.learningStore.bySource).length}
                        </div>
                        <div className="text-xs font-mono text-ink-lo">SOURCES</div>
                      </div>
                      <div className="border border-white/[0.07] p-3 text-center">
                        <div className="text-lg font-mono text-ink-hi">
                          {Object.keys(d.system.vectorDb.learningStore.byType).length}
                        </div>
                        <div className="text-xs font-mono text-ink-lo">ENTITY TYPES</div>
                      </div>
                    </div>

                    {/* By Source breakdown */}
                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                      <div className="border border-white/[0.07] p-3">
                        <div className="text-xs font-mono text-ink-min mb-2">BY SOURCE</div>
                        <div className="space-y-1.5">
                          {Object.entries(d.system.vectorDb.learningStore.bySource)
                            .sort(([, a], [, b]) => (b as number) - (a as number))
                            .map(([source, count]) => {
                              const total = d.system.vectorDb!.learningStore!.totalEntries;
                              const pct = total ? Math.round(((count as number) / total) * 100) : 0;
                              return (
                                <div key={source}>
                                  <div className="flex justify-between text-xs font-mono mb-0.5">
                                    <span className="text-ink">{source}</span>
                                    <span className="text-ink-lo">
                                      {(count as number).toLocaleString()} ({pct}%)
                                    </span>
                                  </div>
                                  <div
                                    className="w-full h-1 bg-white/[0.03] overflow-hidden"
                                    role="progressbar"
                                    aria-valuenow={pct}
                                    aria-valuemin={0}
                                    aria-valuemax={100}
                                    aria-label={`${source} classifications`}
                                  >
                                    <div
                                      className="h-full bg-signal-cyan"
                                      style={{ width: `${Math.max(pct, 1)}%` }}
                                    />
                                  </div>
                                </div>
                              );
                            })}
                        </div>
                      </div>
                      <div className="border border-white/[0.07] p-3">
                        <div className="text-xs font-mono text-ink-min mb-2">BY ENTITY TYPE</div>
                        <div className="space-y-1.5">
                          {Object.entries(d.system.vectorDb.learningStore.byType)
                            .sort(([, a], [, b]) => (b as number) - (a as number))
                            .map(([type, count]) => {
                              const total = d.system.vectorDb!.learningStore!.totalEntries;
                              const pct = total ? Math.round(((count as number) / total) * 100) : 0;
                              return (
                                <div key={type}>
                                  <div className="flex justify-between text-xs font-mono mb-0.5">
                                    <span className="text-ink">{type}</span>
                                    <span className="text-ink-lo">
                                      {(count as number).toLocaleString()} ({pct}%)
                                    </span>
                                  </div>
                                  <div
                                    className="w-full h-1 bg-white/[0.03] overflow-hidden"
                                    role="progressbar"
                                    aria-valuenow={pct}
                                    aria-valuemin={0}
                                    aria-valuemax={100}
                                    aria-label={`${type} classifications`}
                                  >
                                    <div
                                      className="h-full bg-phos"
                                      style={{ width: `${Math.max(pct, 1)}%` }}
                                    />
                                  </div>
                                </div>
                              );
                            })}
                        </div>
                      </div>
                    </div>

                    {/* Confidence Distribution */}
                    {Object.keys(d.system.vectorDb.learningStore.confidenceDistribution).length >
                      0 && (
                      <div className="border border-white/[0.07] p-3 mt-3">
                        <div className="text-xs font-mono text-ink-min mb-2">
                          CONFIDENCE DISTRIBUTION
                        </div>
                        <div className="flex items-end gap-1 h-16">
                          {(() => {
                            const dist = d.system.vectorDb!.learningStore!.confidenceDistribution;
                            const buckets = Array.from({ length: 11 }, (_, i) =>
                              (i / 10).toFixed(1)
                            );
                            const maxCount = Math.max(
                              ...buckets.map((b) => (dist[b] ?? 0) as number),
                              1
                            );
                            return buckets.map((bucket) => {
                              const count = (dist[bucket] ?? 0) as number;
                              const height = count > 0 ? Math.max((count / maxCount) * 100, 5) : 0;
                              return (
                                <div
                                  key={bucket}
                                  className="flex-1 flex flex-col items-center gap-0.5"
                                  title={`${bucket}: ${count} entries`}
                                >
                                  <div
                                    className="w-full flex items-end justify-center"
                                    style={{ height: "48px" }}
                                  >
                                    <div
                                      className="w-full min-w-[4px] bg-signal-cyan transition-all"
                                      style={{ height: `${height}%` }}
                                    />
                                  </div>
                                  <span className="text-xs font-mono text-ink-min">{bucket}</span>
                                </div>
                              );
                            });
                          })()}
                        </div>
                      </div>
                    )}

                    {/* Timestamps */}
                    <div className="flex gap-4 mt-2 text-xs font-mono text-ink-min">
                      {d.system.vectorDb.learningStore.oldestEntry && (
                        <span>
                          oldest: {formatTime(d.system.vectorDb.learningStore.oldestEntry)}
                        </span>
                      )}
                      {d.system.vectorDb.learningStore.newestEntry && (
                        <span>
                          newest: {formatTime(d.system.vectorDb.learningStore.newestEntry)}
                        </span>
                      )}
                    </div>
                  </div>
                )}
              </>
            )}
          </div>
        </div>
      )}

      {/* LLM Stats */}
      {d?.llm && Object.keys(d.llm).length > 0 && (
        <div className="panel">
          <TerminalTitlebar title="Model stats" />
          <div className="p-4">
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-sm font-mono">
              {Object.entries(d.llm)
                .filter(([, val]) => val === null || typeof val !== "object")
                .map(([key, val]) => (
                  <div key={key}>
                    <span className="text-ink-lo text-xs block">
                      {key.replace(/_/g, " ").toUpperCase()}
                    </span>
                    <span>{val === null ? "—" : String(val)}</span>
                  </div>
                ))}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
