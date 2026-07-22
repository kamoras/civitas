"use client";

import { Suspense, useEffect, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import Navbar from "@/components/layout/Navbar";
import TerminalTitlebar from "@/components/TerminalTitlebar";
import MatrixRain from "@/components/effects/MatrixRain";
import Footer from "@/components/layout/Footer";
import BackToTop from "@/components/BackToTop";
import GlitchText from "@/components/effects/GlitchText";
import BillStageFlow, { ALL_STAGE_CODES } from "@/components/bills/BillStageFlow";
import BillStageGroup from "@/components/bills/BillStageGroup";
import BillRow from "@/components/bills/BillRow";
import { fetchBillsInFlight } from "@/lib/api";
import type { BillInFlight } from "@/types/bill";

type ChamberFilter = "all" | "senate" | "house";
type PartyFilter = "ALL" | "D" | "R" | "I";
type ViewMode = "hot" | "all";

const PER_PAGE = 50;

function BillsPageContent() {
  const searchParams = useSearchParams();
  // Deep link from a scorecard's sponsored-bill list (?q=<billId>) — land
  // in "all" mode since "hot" only shows bills with a live Action Center
  // mention, which most individual bills don't have.
  const initialQ = searchParams.get("q") || "";

  const [stageCounts, setStageCounts] = useState<Record<string, number>>({});

  const [mode, setMode] = useState<ViewMode>(initialQ ? "all" : "hot");
  const [stage, setStage] = useState<string | null>(null);
  const [chamber, setChamber] = useState<ChamberFilter>("all");
  const [party, setParty] = useState<PartyFilter>("ALL");
  const [search, setSearch] = useState(initialQ);

  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [debouncedSearch, setDebouncedSearch] = useState(initialQ);

  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => setDebouncedSearch(search), 300);
    return () => { if (debounceRef.current) clearTimeout(debounceRef.current); };
  }, [search]);

  // The stage-flow funnel is a global overview of the whole pipeline,
  // independent of the chamber/party/search filters below it — fetch it
  // once, decoupled from everything else on the page.
  useEffect(() => {
    let cancelled = false;
    fetchBillsInFlight({ sort: "recent", page: 1, perPage: 1 })
      .then((res) => { if (!cancelled) setStageCounts(res.stageCounts); })
      .catch(() => {});
    return () => { cancelled = true; };
  }, []);

  const chamberParam = chamber === "all" ? undefined : chamber;
  const partyParam = party === "ALL" ? undefined : party;
  const qParam = debouncedSearch || undefined;

  // REFERRED (2026-07) is the automatic, universal first step every bill
  // gets within days of introduction — not evidence anyone's done
  // anything with it, so it's excluded from "in motion" the same as
  // INTRODUCED (see bill_stage.py's module docstring).
  const totalMoving = Object.entries(stageCounts)
    .filter(([code]) => code !== "INTRODUCED" && code !== "REFERRED")
    .reduce((sum, [, count]) => sum + count, 0);

  return (
    <div className="min-h-screen bg-crt-black text-matrix-green">
      <MatrixRain />
      <Navbar />
      <main id="main-content" tabIndex={-1} className="pt-24 pb-16 px-4">
        <div className="max-w-6xl mx-auto">

          <div className="text-center mb-8">
            <GlitchText
              as="h1"
              text="BILLS IN MOTION"
              className="font-pixel text-xl sm:text-3xl text-matrix-green neon-green mb-2 block"
            />
            <p className="font-mono text-xs text-matrix-green/40">
              WHERE {totalMoving.toLocaleString()} BILLS SIT IN THE LEGISLATIVE PIPELINE RIGHT NOW
            </p>
          </div>

          <div className="flex justify-center gap-2 mb-4">
            <button
              onClick={() => setMode("hot")}
              className={`font-mono text-xs px-4 py-1.5 border transition-colors uppercase tracking-widest ${
                mode === "hot"
                  ? "border-neon-cyan text-neon-cyan bg-neon-cyan/10"
                  : "border-matrix-green/15 text-matrix-green/40 hover:text-matrix-green/70"
              }`}
            >
              Active Now
            </button>
            <button
              onClick={() => setMode("all")}
              className={`font-mono text-xs px-4 py-1.5 border transition-colors uppercase tracking-widest ${
                mode === "all"
                  ? "border-neon-cyan text-neon-cyan bg-neon-cyan/10"
                  : "border-matrix-green/15 text-matrix-green/40 hover:text-matrix-green/70"
              }`}
            >
              All Bills
            </button>
          </div>

          <TerminalTitlebar title="bill_pipeline.dat" />
          <div className="border border-t-0 border-matrix-green/20 bg-crt-black/40 p-4 mb-6">
            <BillStageFlow
              stageCounts={stageCounts}
              activeStage={stage}
              onSelectStage={setStage}
            />

            <div className="flex flex-wrap gap-3 items-center mt-5 pt-4 border-t border-matrix-green/10">
              <input
                type="text"
                placeholder="SEARCH TITLE..."
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                className="font-mono text-xs bg-crt-black border border-matrix-green/20 focus:border-matrix-green/60 text-matrix-green placeholder-matrix-green/25 px-3 py-1.5 outline-none w-48"
              />

              <div className="flex gap-1">
                {(["all", "senate", "house"] as ChamberFilter[]).map((c) => (
                  <button
                    key={c}
                    onClick={() => setChamber(c)}
                    className={`font-mono text-[10px] px-2 py-1 border transition-colors uppercase ${
                      chamber === c
                        ? "border-neon-cyan text-neon-cyan bg-neon-cyan/10"
                        : "border-matrix-green/15 text-matrix-green/35 hover:text-matrix-green/60"
                    }`}
                  >
                    {c}
                  </button>
                ))}
              </div>

              <div className="flex gap-1">
                {(["ALL", "D", "R", "I"] as PartyFilter[]).map((p) => (
                  <button
                    key={p}
                    onClick={() => setParty(p)}
                    className={`font-mono text-[10px] px-2 py-1 border transition-colors ${
                      party === p
                        ? "border-matrix-green/60 text-matrix-green bg-matrix-green/10"
                        : "border-matrix-green/15 text-matrix-green/35 hover:text-matrix-green/60"
                    }`}
                  >
                    {p}
                  </button>
                ))}
              </div>

              {(stage || chamber !== "all" || party !== "ALL" || search) && (
                <button
                  onClick={() => { setStage(null); setChamber("all"); setParty("ALL"); setSearch(""); }}
                  className="font-mono text-[9px] text-matrix-green/30 hover:text-matrix-green/60 transition-colors tracking-widest"
                >
                  CLEAR
                </button>
              )}
            </div>
          </div>

          {mode === "hot" ? (
            <HotBillsList
              stage={stage}
              chamber={chamberParam}
              party={partyParam}
              q={qParam}
              onViewAll={() => setMode("all")}
            />
          ) : stage ? (
            <BillStageGroup stageCode={stage} chamber={chamberParam} party={partyParam} q={qParam} forceExpanded />
          ) : (
            <AllBillsGroups chamber={chamberParam} party={partyParam} q={qParam} />
          )}

        </div>
      </main>
      <BackToTop />
      <Footer />
    </div>
  );
}

export default function BillsPage() {
  return (
    <Suspense fallback={null}>
      <BillsPageContent />
    </Suspense>
  );
}

function AllBillsGroups({
  chamber, party, q,
}: {
  chamber?: "senate" | "house";
  party?: "D" | "R" | "I";
  q?: string;
}) {
  const [anyResults, setAnyResults] = useState<boolean | null>(null);

  useEffect(() => {
    let cancelled = false;
    setAnyResults(null);
    fetchBillsInFlight({ chamber, party, q, sort: "recent", page: 1, perPage: 1 })
      .then((res) => { if (!cancelled) setAnyResults(res.total > 0); })
      .catch(() => { if (!cancelled) setAnyResults(true); }); // fail open — let the groups themselves surface the error
    return () => { cancelled = true; };
  }, [chamber, party, q]);

  if (anyResults === null) {
    return (
      <div className="text-center py-16 font-mono text-xs text-matrix-green/30 tracking-widest animate-pulse">
        LOADING...
      </div>
    );
  }
  if (!anyResults) {
    return (
      <div className="text-center py-16 font-mono text-xs text-matrix-green/30 tracking-widest">
        NO RESULTS
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-2">
      {ALL_STAGE_CODES.map((code) => (
        <BillStageGroup key={code} stageCode={code} chamber={chamber} party={party} q={q} />
      ))}
    </div>
  );
}

function HotBillsList({
  stage, chamber, party, q, onViewAll,
}: {
  stage: string | null;
  chamber?: "senate" | "house";
  party?: "D" | "R" | "I";
  q?: string;
  onViewAll: () => void;
}) {
  const [results, setResults] = useState<BillInFlight[]>([]);
  const [total, setTotal] = useState(0);
  const [totalPages, setTotalPages] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => { setPage(1); }, [stage, chamber, party, q]);

  useEffect(() => {
    let cancelled = false;
    const isFirstPage = page === 1;
    if (isFirstPage) setLoading(true); else setLoadingMore(true);

    fetchBillsInFlight({ stage: stage ?? undefined, chamber, party, q, sort: "hot", page, perPage: PER_PAGE })
      .then((res) => {
        if (cancelled) return;
        setResults((prev) => (isFirstPage ? res.bills : [...prev, ...res.bills]));
        setTotal(res.total);
        setTotalPages(res.totalPages);
        setError(null);
      })
      .catch((err) => { if (!cancelled) setError(err.message || "Failed to load bills"); })
      .finally(() => { if (!cancelled) { setLoading(false); setLoadingMore(false); } });

    return () => { cancelled = true; };
  }, [stage, chamber, party, q, page]);

  if (loading) {
    return (
      <div className="text-center py-16 font-mono text-xs text-matrix-green/30 tracking-widest animate-pulse">
        LOADING...
      </div>
    );
  }
  if (error) {
    return <div className="text-center py-16 font-mono text-xs text-red-400/60">{error}</div>;
  }
  if (results.length === 0) {
    return (
      <div className="text-center py-16 font-mono text-xs text-matrix-green/30 tracking-widest space-y-3">
        <p>NOTHING CURRENTLY TRENDING IN ACTION CENTER FOR THIS FILTER</p>
        <button onClick={onViewAll} className="font-mono text-[10px] text-neon-cyan hover:underline tracking-widest">
          VIEW ALL BILLS INSTEAD
        </button>
      </div>
    );
  }

  return (
    <>
      <p className="font-mono text-[10px] text-matrix-green/30 mb-3 tracking-widest">
        SHOWING {results.length.toLocaleString()} OF {total.toLocaleString()} BILL{total !== 1 ? "S" : ""}
      </p>
      <div className="border border-matrix-green/10 divide-y divide-matrix-green/10">
        {results.map((bill) => (
          <BillRow key={`${bill.chamber}-${bill.billId}-${bill.sponsorId}`} bill={bill} />
        ))}
      </div>

      {page < totalPages && (
        <div className="flex justify-center mt-6">
          <button
            onClick={() => setPage((p) => p + 1)}
            disabled={loadingMore}
            className="font-mono text-[10px] tracking-widest px-4 py-2 border border-matrix-green/20 text-matrix-green/60 hover:text-matrix-green hover:border-matrix-green/40 disabled:opacity-40 disabled:cursor-wait transition-colors"
          >
            {loadingMore ? "LOADING..." : `LOAD MORE (${(total - results.length).toLocaleString()} REMAINING)`}
          </button>
        </div>
      )}
    </>
  );
}
