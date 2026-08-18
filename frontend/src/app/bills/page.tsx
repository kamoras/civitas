"use client";

import { Suspense, useEffect, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import Navbar from "@/components/layout/Navbar";
import TerminalTitlebar from "@/components/TerminalTitlebar";
import Footer from "@/components/layout/Footer";
import PageMasthead from "@/components/layout/PageMasthead";
import BackToTop from "@/components/BackToTop";
import BillStageFlow, { ALL_STAGE_CODES } from "@/components/bills/BillStageFlow";
import BillStageGroup from "@/components/bills/BillStageGroup";
import BillRow from "@/components/bills/BillRow";
import { fetchBillsInFlight } from "@/lib/api";
import { useAsyncData } from "@/hooks/useAsyncData";
import type { BillInFlight } from "@/types/bill";

type ChamberFilter = "all" | "senate" | "house";
type PartyFilter = "ALL" | "D" | "R" | "I";
type ViewMode = "hot" | "all";

const EMPTY_BILLS: BillInFlight[] = [];
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
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, [search]);

  // The stage-flow funnel is a global overview of the whole pipeline,
  // independent of the chamber/party/search filters below it — fetch it
  // once, decoupled from everything else on the page.
  useEffect(() => {
    let cancelled = false;
    fetchBillsInFlight({ sort: "recent", page: 1, perPage: 1 })
      .then((res) => {
        if (!cancelled) setStageCounts(res.stageCounts);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
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
    <div className="min-h-screen bg-surface-base text-ink-hi">
      <Navbar />
      <main id="main-content" tabIndex={-1} className="pt-[var(--header-clearance)] pb-16 px-4">
        <div className="max-w-6xl mx-auto">
          <PageMasthead
            className="mb-8"
            eyebrow="Bills · legislative pipeline"
            title="Bills in motion"
          >
            <p>
              Where {totalMoving.toLocaleString()} bills sit in the legislative pipeline right now.
            </p>
            {/* Verified live (2026-08 review): this total is genuinely
                correct — it's the pipeline breakdown below MINUS
                Introduced and Referred — but without this line it reads
                as a bug next to a "Referred to Committee" count in the
                tens of thousands sitting directly below it. */}
            <p className="font-mono text-xs text-ink-min mt-1">
              Excludes bills only introduced or automatically referred to committee — nearly every
              bill clears that step within days; this counts what&apos;s moved further.
            </p>
          </PageMasthead>

          <div className="flex justify-center gap-2 mb-4">
            <button
              onClick={() => setMode("hot")}
              className={`font-mono text-xs px-4 py-1.5 border transition-colors uppercase tracking-widest ${
                mode === "hot"
                  ? "border-signal-cyan/40 text-signal-cyan bg-signal-cyan/10"
                  : "border-white/[0.07] text-ink-min hover:text-phos"
              }`}
            >
              Active Now
            </button>
            <button
              onClick={() => setMode("all")}
              className={`font-mono text-xs px-4 py-1.5 border transition-colors uppercase tracking-widest ${
                mode === "all"
                  ? "border-signal-cyan/40 text-signal-cyan bg-signal-cyan/10"
                  : "border-white/[0.07] text-ink-min hover:text-phos"
              }`}
            >
              All Bills
            </button>
          </div>

          <TerminalTitlebar title="Pipeline" />
          <div className="border border-t-0 border-white/[0.07] bg-surface-base p-4 mb-6">
            <BillStageFlow stageCounts={stageCounts} activeStage={stage} onSelectStage={setStage} />

            <div className="flex flex-wrap gap-3 items-center mt-5 pt-4 border-t border-white/[0.07]">
              <input
                type="text"
                placeholder="SEARCH TITLE..."
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                className="font-mono text-xs bg-surface-base border border-white/[0.07] focus:border-phos/40 text-ink-hi placeholder-white/15 px-3 py-1.5 outline-none w-48"
              />

              <div className="flex gap-1">
                {(["all", "senate", "house"] as ChamberFilter[]).map((c) => (
                  <button
                    key={c}
                    onClick={() => setChamber(c)}
                    className={`font-mono text-xs px-2 py-1 border transition-colors uppercase ${
                      chamber === c
                        ? "border-signal-cyan/40 text-signal-cyan bg-signal-cyan/10"
                        : "border-white/[0.07] text-ink-min hover:text-phos"
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
                    className={`font-mono text-xs px-2 py-1 border transition-colors ${
                      party === p
                        ? "border-phos/40 text-ink-hi bg-white/[0.03]"
                        : "border-white/[0.07] text-ink-min hover:text-phos"
                    }`}
                  >
                    {p}
                  </button>
                ))}
              </div>

              {(stage || chamber !== "all" || party !== "ALL" || search) && (
                <button
                  onClick={() => {
                    setStage(null);
                    setChamber("all");
                    setParty("ALL");
                    setSearch("");
                  }}
                  className="font-mono text-xs text-ink-min hover:text-phos transition-colors tracking-widest"
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
            <BillStageGroup
              stageCode={stage}
              chamber={chamberParam}
              party={partyParam}
              q={qParam}
              forceExpanded
            />
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
  chamber,
  party,
  q,
}: {
  chamber?: "senate" | "house";
  party?: "D" | "R" | "I";
  q?: string;
}) {
  // One request supplies every group's header: the response's stageCounts
  // already reflects the chamber/party/q filters server-side, so the
  // groups no longer each probe for their own count (which used to fan
  // out ~8 parallel requests per filter change).
  // Fail open: on error the groups fall back to probing their own counts.
  const totalsRequest = useAsyncData(`stage-totals:${chamber}:${party}:${q}`, () =>
    fetchBillsInFlight({ chamber, party, q, sort: "recent", page: 1, perPage: 1 })
  );
  const stageTotals: Record<string, number> | "loading" | "error" = totalsRequest.loading
    ? "loading"
    : (totalsRequest.data?.stageCounts ?? "error");

  if (stageTotals === "loading") {
    return (
      <div className="text-center py-16 font-mono text-xs text-ink-min tracking-widest animate-pulse">
        LOADING...
      </div>
    );
  }
  if (stageTotals !== "error" && ALL_STAGE_CODES.every((code) => !(stageTotals[code] > 0))) {
    return (
      <div className="text-center py-16 font-mono text-xs text-ink-min tracking-widest">
        NO RESULTS
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-2">
      {ALL_STAGE_CODES.map((code) => (
        <BillStageGroup
          key={code}
          stageCode={code}
          count={stageTotals === "error" ? undefined : (stageTotals[code] ?? 0)}
          chamber={chamber}
          party={party}
          q={q}
        />
      ))}
    </div>
  );
}

function HotBillsList({
  stage,
  chamber,
  party,
  q,
  onViewAll,
}: {
  stage: string | null;
  chamber?: "senate" | "house";
  party?: "D" | "R" | "I";
  q?: string;
  onViewAll: () => void;
}) {
  /* One object rather than five pieces of state, because they are only ever
     meaningful together: a bill list belongs to a filter signature and a page
     depth, and splitting them is what made "is this loading?" need its own
     stored flag. Only ever written from a settled promise, so no setState runs
     synchronously inside the effect below. */
  const [loaded, setLoaded] = useState<{
    sig: string;
    page: number;
    bills: BillInFlight[];
    total: number;
    totalPages: number;
    error: string | null;
  } | null>(null);
  /* Page is stored against the filter signature it belongs to, so changing a
     filter resets to page 1 by derivation rather than by an effect that fires
     setPage(1) and forces a second render (during which the old page's request
     is already in flight). */
  const filterSig = `${stage ?? ""}:${chamber}:${party}:${q}`;
  const [pageState, setPageState] = useState({ sig: filterSig, page: 1 });
  const page = pageState.sig === filterSig ? pageState.page : 1;
  const setPage = (next: number | ((p: number) => number)) =>
    setPageState((prev) => ({
      sig: filterSig,
      page: typeof next === "function" ? next(prev.sig === filterSig ? prev.page : 1) : next,
    }));
  const current = loaded?.sig === filterSig ? loaded : null;
  const results = current?.bills ?? EMPTY_BILLS;
  const total = current?.total ?? 0;
  const totalPages = current?.totalPages ?? 0;
  const error = current?.error ?? null;
  // Derived, not stored: nothing for these filters yet means the first page is
  // in flight; having fewer pages than asked for means "load more" is.
  const loading = current === null;
  const loadingMore = current !== null && current.page < page;

  useEffect(() => {
    if (loaded?.sig === filterSig && loaded.page >= page) return;
    let cancelled = false;
    const isFirstPage = page === 1;

    fetchBillsInFlight({
      stage: stage ?? undefined,
      chamber,
      party,
      q,
      sort: "hot",
      page,
      perPage: PER_PAGE,
    })
      .then((res) => {
        if (cancelled) return;
        setLoaded((prev) => ({
          sig: filterSig,
          page,
          bills: isFirstPage || prev?.sig !== filterSig ? res.bills : [...prev.bills, ...res.bills],
          total: res.total,
          totalPages: res.totalPages,
          error: null,
        }));
      })
      .catch((err) => {
        if (cancelled) return;
        setLoaded((prev) => ({
          sig: filterSig,
          page,
          bills: prev?.sig === filterSig ? prev.bills : [],
          total: prev?.sig === filterSig ? prev.total : 0,
          totalPages: prev?.sig === filterSig ? prev.totalPages : 0,
          error: err instanceof Error ? err.message : "Failed to load bills",
        }));
      });

    return () => {
      cancelled = true;
    };
  }, [stage, chamber, party, q, page, filterSig, loaded]);

  if (loading) {
    return (
      <div className="text-center py-16 font-mono text-xs text-ink-min tracking-widest animate-pulse">
        LOADING...
      </div>
    );
  }
  if (error) {
    return <div className="text-center py-16 font-mono text-xs text-signal-red">{error}</div>;
  }
  if (results.length === 0) {
    return (
      <div className="text-center py-16 font-mono text-xs text-ink-min tracking-widest space-y-3">
        <p>NOTHING CURRENTLY TRENDING IN ACTION CENTER FOR THIS FILTER</p>
        <button
          onClick={onViewAll}
          className="font-mono text-xs text-signal-cyan hover:underline tracking-widest"
        >
          VIEW ALL BILLS INSTEAD
        </button>
      </div>
    );
  }

  return (
    <>
      <p className="font-mono text-xs text-ink-min mb-3 tracking-widest">
        SHOWING {results.length.toLocaleString()} OF {total.toLocaleString()} BILL
        {total !== 1 ? "S" : ""}
      </p>
      <div className="border border-white/[0.07] divide-y divide-white/[0.07]">
        {results.map((bill) => (
          <BillRow key={`${bill.chamber}-${bill.billId}-${bill.sponsorId}`} bill={bill} />
        ))}
      </div>

      {page < totalPages && (
        <div className="flex justify-center mt-6">
          <button
            onClick={() => setPage((p) => p + 1)}
            disabled={loadingMore}
            className="font-mono text-xs tracking-widest px-4 py-2 border border-white/[0.07] text-ink-lo hover:text-phos hover:border-white/15 disabled:opacity-40 disabled:cursor-wait transition-colors"
          >
            {loadingMore
              ? "LOADING..."
              : `LOAD MORE (${(total - results.length).toLocaleString()} REMAINING)`}
          </button>
        </div>
      )}
    </>
  );
}
