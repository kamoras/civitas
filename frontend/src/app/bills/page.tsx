"use client";

import { useEffect, useRef, useState } from "react";
import Navbar from "@/components/layout/Navbar";
import TerminalTitlebar from "@/components/TerminalTitlebar";
import MatrixRain from "@/components/effects/MatrixRain";
import Footer from "@/components/layout/Footer";
import BackToTop from "@/components/BackToTop";
import GlitchText from "@/components/effects/GlitchText";
import BillStageFlow from "@/components/bills/BillStageFlow";
import BillCard from "@/components/bills/BillCard";
import { fetchBillsInFlight } from "@/lib/api";
import type { PaginatedBills } from "@/types/bill";

type ChamberFilter = "all" | "senate" | "house";
type PartyFilter = "ALL" | "D" | "R" | "I";

const PER_PAGE = 24;

export default function BillsPage() {
  const [data, setData] = useState<PaginatedBills | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [stage, setStage] = useState<string | null>(null);
  const [chamber, setChamber] = useState<ChamberFilter>("all");
  const [party, setParty] = useState<PartyFilter>("ALL");
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);

  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [debouncedSearch, setDebouncedSearch] = useState("");

  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => setDebouncedSearch(search), 300);
    return () => { if (debounceRef.current) clearTimeout(debounceRef.current); };
  }, [search]);

  useEffect(() => { setPage(1); }, [stage, chamber, party, debouncedSearch]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    fetchBillsInFlight({
      stage: stage ?? undefined,
      chamber: chamber === "all" ? undefined : chamber,
      party: party === "ALL" ? undefined : party,
      q: debouncedSearch || undefined,
      page,
      perPage: PER_PAGE,
    })
      .then((res) => { if (!cancelled) { setData(res); setError(null); } })
      .catch((err) => { if (!cancelled) setError(err.message || "Failed to load bills"); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [stage, chamber, party, debouncedSearch, page]);

  const stageCounts = data?.stageCounts ?? {};
  const totalMoving = Object.entries(stageCounts)
    .filter(([code]) => code !== "INTRODUCED")
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

          {loading ? (
            <div className="text-center py-16 font-mono text-xs text-matrix-green/30 tracking-widest animate-pulse">
              LOADING...
            </div>
          ) : error ? (
            <div className="text-center py-16 font-mono text-xs text-red-400/60">{error}</div>
          ) : !data || data.bills.length === 0 ? (
            <div className="text-center py-16 font-mono text-xs text-matrix-green/30 tracking-widest">
              NO RESULTS
            </div>
          ) : (
            <>
              <p className="font-mono text-[10px] text-matrix-green/30 mb-3 tracking-widest">
                {data.total.toLocaleString()} BILL{data.total !== 1 ? "S" : ""}
              </p>
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
                {data.bills.map((bill) => (
                  <BillCard key={`${bill.chamber}-${bill.billId}-${bill.sponsorId}`} bill={bill} />
                ))}
              </div>

              {data.totalPages > 1 && (
                <div className="flex items-center justify-center gap-4 mt-8">
                  <button
                    onClick={() => setPage((p) => Math.max(1, p - 1))}
                    disabled={page <= 1}
                    className="font-mono text-[10px] tracking-widest px-3 py-1.5 border border-matrix-green/20 text-matrix-green/60 hover:text-matrix-green hover:border-matrix-green/40 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
                  >
                    PREV
                  </button>
                  <span className="font-mono text-[10px] text-matrix-green/40">
                    {page} / {data.totalPages}
                  </span>
                  <button
                    onClick={() => setPage((p) => Math.min(data.totalPages, p + 1))}
                    disabled={page >= data.totalPages}
                    className="font-mono text-[10px] tracking-widest px-3 py-1.5 border border-matrix-green/20 text-matrix-green/60 hover:text-matrix-green hover:border-matrix-green/40 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
                  >
                    NEXT
                  </button>
                </div>
              )}
            </>
          )}

        </div>
      </main>
      <BackToTop />
      <Footer />
    </div>
  );
}
