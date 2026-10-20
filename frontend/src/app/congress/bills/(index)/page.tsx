"use client";

import { Suspense, useEffect, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import Navbar from "@/components/layout/Navbar";
import TerminalTitlebar from "@/components/TerminalTitlebar";
import Footer from "@/components/layout/Footer";
import PageFallback from "@/components/layout/PageFallback";
import PageMasthead from "@/components/layout/PageMasthead";
import SectionTabs from "@/components/layout/SectionTabs";
import BackToTop from "@/components/BackToTop";
import BillStageFlow, { ALL_STAGE_CODES } from "@/components/bills/BillStageFlow";
import BillStageGroup from "@/components/bills/BillStageGroup";
import BillRow from "@/components/bills/BillRow";
import { fetchBillsInFlight } from "@/lib/api";
import { useAsyncData } from "@/hooks/useAsyncData";
import { useBillsInitial } from "./BillsInitial";
import type { BillInFlight, PaginatedBills } from "@/types/bill";
import { formatUtcDate } from "@/lib/formatting";
import { BOXED_CONTROL } from "@/lib/controlStyles";

type ChamberFilter = "all" | "senate" | "house";
type PartyFilter = "ALL" | "D" | "R" | "I";
type ViewMode = "hot" | "all";

const EMPTY_BILLS: BillInFlight[] = [];
const shortDay = (iso: string) =>
  formatUtcDate(iso, { weekday: "long", month: "long", day: "numeric" });
const PER_PAGE = 50;

function BillsPageContent() {
  const searchParams = useSearchParams();
  // Deep link from a scorecard's sponsored-bill list (?q=<billId>) — land
  // in "all" mode since "hot" only shows bills with a live Action Center
  // mention, which most individual bills don't have.
  const initialQ = searchParams.get("q") || "";
  // From /congress's pipeline rows (?reached=<milestone stage>): this
  // Congress's bills that got at least that far. Latched at mount, like
  // ?q=: the page writes the URL itself (replaceState) when it is cleared,
  // and that write must not feed back in as a new deep link.
  const [reached, setReached] = useState<string | null>(() => searchParams.get("reached"));

  // The overview the server read, when it could (congress/bills/layout.tsx).
  const initial = useBillsInitial();
  const [stageCounts, setStageCounts] = useState<Record<string, number>>(
    () => initial?.stageCounts ?? {}
  );
  const [inMotion, setInMotion] = useState(() => initial?.inMotion ?? 0);

  const [mode, setMode] = useState<ViewMode>(initialQ || reached ? "all" : "hot");
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
  const haveOverview = initial !== null;
  useEffect(() => {
    if (haveOverview) return;
    let cancelled = false;
    fetchBillsInFlight({ sort: "recent", page: 1, perPage: 1 })
      .then((res) => {
        if (cancelled) return;
        setStageCounts(res.stageCounts);
        setInMotion(res.inMotion ?? 0);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [haveOverview]);

  // window.history, never router.replace: on this route the router's
  // replace can leave the address bar unchanged (AGENTS.md, "Client-side URL
  // state"), and a reload would bring the cleared filter back.
  const clearReached = () => {
    if (!reached) return;
    setReached(null);
    const url = new URL(window.location.href);
    url.searchParams.delete("reached");
    window.history.replaceState(null, "", url);
  };
  // A milestone counts bills that got at least that far, a stage pick bills
  // at exactly that stage: picking one drops the other rather than showing
  // their overlap under either's name.
  const selectStage = (next: string | null) => {
    if (next) clearReached();
    setStage(next);
  };

  const chamberParam = chamber === "all" ? undefined : chamber;
  const partyParam = party === "ALL" ? undefined : party;
  const qParam = debouncedSearch || undefined;

  return (
    <div className="min-h-screen bg-surface-base text-ink-hi">
      <Navbar />
      <main id="main-content" tabIndex={-1} className="pt-[var(--header-clearance)] pb-16 px-4">
        <div className="max-w-6xl mx-auto">
          <SectionTabs section="congress" active="bills" />
          <PageMasthead
            className="mb-8"
            eyebrow="Bills · legislative pipeline"
            title="Bills in motion"
          >
            <p>
              Where {inMotion.toLocaleString()} bills sit in the legislative pipeline right now.
            </p>
            {/* Verified live (2026-08 review): this total is genuinely
                correct — it's the pipeline breakdown below MINUS
                Introduced and Referred — but without this line it reads
                as a bug next to a "Referred to Committee" count in the
                tens of thousands sitting directly below it. */}
            <p className="mt-1 font-sans text-xs text-ink-min">
              Excludes bills only introduced or automatically referred to committee: nearly every
              bill clears that step within days; this counts what&apos;s moved further.
            </p>
          </PageMasthead>

          <div className="flex justify-center gap-2 mb-4">
            <button
              onClick={() => {
                clearReached();
                setMode("hot");
              }}
              aria-pressed={mode === "hot"}
              className={`font-mono text-xs px-4 py-1.5 border transition-colors ${
                mode === "hot" ? BOXED_CONTROL.selected : BOXED_CONTROL.unselected
              }`}
            >
              Active Now
            </button>
            <button
              onClick={() => setMode("all")}
              aria-pressed={mode === "all"}
              className={`font-mono text-xs px-4 py-1.5 border transition-colors ${
                mode === "all" ? BOXED_CONTROL.selected : BOXED_CONTROL.unselected
              }`}
            >
              All Bills
            </button>
          </div>

          <TerminalTitlebar title="Pipeline" />
          <div className="border border-t-0 border-white/[0.07] bg-surface-base p-4 mb-6">
            <BillStageFlow
              stageCounts={stageCounts}
              activeStage={stage}
              onSelectStage={selectStage}
            />

            <div className="flex flex-wrap gap-3 items-center mt-5 pt-4 border-t border-white/[0.07]">
              <input
                type="text"
                placeholder="Title, sponsor or number"
                aria-label="Search bills by title, sponsor or bill number"
                value={search}
                onChange={(e) => {
                  setSearch(e.target.value);
                  // "Active now" holds only the few bills an issue names, so a
                  // search there found almost nothing: search every bill, as a
                  // ?q= deep link already does.
                  if (e.target.value.trim()) setMode("all");
                }}
                className="font-mono text-xs bg-surface-base border border-white/[0.07] focus:border-phos/40 text-ink-hi placeholder:text-ink-min px-3 py-1.5 outline-none w-60"
              />

              <div className="flex flex-wrap gap-1" role="group" aria-label="Filter by chamber">
                {(["all", "senate", "house"] as ChamberFilter[]).map((c) => (
                  <button
                    key={c}
                    onClick={() => setChamber(c)}
                    aria-pressed={chamber === c}
                    className={`font-mono text-xs px-2 py-1 border transition-colors ${
                      chamber === c ? BOXED_CONTROL.selected : BOXED_CONTROL.unselected
                    }`}
                  >
                    {c}
                  </button>
                ))}
              </div>

              <div
                className="flex flex-wrap gap-1"
                role="group"
                aria-label="Filter by sponsor's party"
              >
                {(["ALL", "D", "R", "I"] as PartyFilter[]).map((p) => (
                  <button
                    key={p}
                    onClick={() => setParty(p)}
                    aria-pressed={party === p}
                    className={`font-mono text-xs px-2 py-1 border transition-colors ${
                      party === p ? BOXED_CONTROL.selected : BOXED_CONTROL.unselected
                    }`}
                  >
                    {p === "ALL" ? "All" : p}
                  </button>
                ))}
              </div>

              {(stage || reached || chamber !== "all" || party !== "ALL" || search) && (
                <button
                  onClick={() => {
                    clearReached();
                    setStage(null);
                    setChamber("all");
                    setParty("ALL");
                    setSearch("");
                  }}
                  className="font-mono text-xs text-ink-min hover:text-phos transition-colors"
                >
                  Clear
                </button>
              )}
            </div>
          </div>

          {reached ? (
            <ReachedBillsList
              reached={reached}
              chamber={chamberParam}
              party={partyParam}
              q={qParam}
              onClear={clearReached}
            />
          ) : mode === "hot" ? (
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
    <Suspense
      fallback={
        <PageFallback eyebrow={"Bills · legislative pipeline"} title={"Bills in motion"} rows={5} />
      }
    >
      <BillsPageContent />
    </Suspense>
  );
}

/** This Congress's bills that got at least as far as a milestone, the very
 * bills its row on /congress counts (GET /api/bills?reached=), newest
 * action first. The chamber, party and search filters narrow it, and the
 * heading then says the count is filtered, so it never passes for the
 * row's. */
function ReachedBillsList({
  reached,
  chamber,
  party,
  q,
  onClear,
}: {
  reached: string;
  chamber?: "senate" | "house";
  party?: "D" | "R" | "I";
  q?: string;
  onClear: () => void;
}) {
  const sig = `${reached}:${chamber}:${party}:${q}`;
  const [loaded, setLoaded] = useState<{
    sig: string;
    page: number;
    bills: BillInFlight[];
    total: number;
    totalPages: number;
    label: string | null;
    error: boolean;
  } | null>(null);
  const [want, setWant] = useState({ sig, page: 1 });
  const page = want.sig === sig ? want.page : 1;
  const current = loaded?.sig === sig ? loaded : null;

  useEffect(() => {
    if (loaded?.sig === sig && loaded.page >= page) return;
    let cancelled = false;
    fetchBillsInFlight({ reached, chamber, party, q, sort: "recent", page, perPage: PER_PAGE })
      .then((res) => {
        if (cancelled) return;
        setLoaded((prev) => ({
          sig,
          page,
          bills: page > 1 && prev?.sig === sig ? [...prev.bills, ...res.bills] : res.bills,
          total: res.total,
          totalPages: res.totalPages,
          label: res.reachedLabel ?? null,
          error: false,
        }));
      })
      .catch(() => {
        if (cancelled) return;
        if (page > 1 && loaded?.sig === sig) {
          // A failed "load more" keeps what is shown and offers the page again.
          setLoaded({ ...loaded, error: true });
          setWant({ sig, page: loaded.page });
          return;
        }
        setLoaded({ sig, page, bills: [], total: 0, totalPages: 0, label: null, error: true });
      });
    return () => {
      cancelled = true;
    };
  }, [reached, chamber, party, q, page, sig, loaded]);

  // The list sits below the funnel and the filters, a screen and a half
  // down on a phone: a reader who picked a row on /congress is brought to
  // it once, when it first arrives, and never moved again after that.
  const sectionRef = useRef<HTMLElement>(null);
  const arrived = useRef(false);
  const ready = current !== null;
  useEffect(() => {
    if (!ready || arrived.current) return;
    arrived.current = true;
    sectionRef.current?.scrollIntoView?.({ block: "start" });
  }, [ready]);

  const filtered = Boolean(chamber || party || q);
  const bills = current?.bills ?? EMPTY_BILLS;
  const total = current?.total ?? 0;

  return (
    <section ref={sectionRef} aria-labelledby="reached-heading">
      <div className="mb-3 flex flex-wrap items-baseline justify-between gap-x-4 gap-y-2">
        <h2 id="reached-heading" className="text-lg text-ink-hi">
          {current?.label ?? "Bills this Congress"}
          {current && !current.error && (
            <>
              {" "}
              <span className="font-mono tabular-nums text-ink-lo">
                {`(${total.toLocaleString()}${filtered ? " matching the filters above" : ""})`}
              </span>
            </>
          )}
        </h2>
        <button
          type="button"
          onClick={onClear}
          className="min-h-6 font-mono text-xs text-ink-lo underline decoration-white/30 underline-offset-4 hover:text-phos"
        >
          Show every bill in motion instead
        </button>
      </div>
      {current === null ? (
        <p className="py-16 text-center font-mono text-xs text-ink-min pulse-subtle">Loading…</p>
      ) : current.error && bills.length === 0 ? (
        <p className="py-16 text-center font-mono text-xs text-signal-red">
          These bills could not be loaded. Try again in a moment.
        </p>
      ) : bills.length === 0 ? (
        <p className="py-16 text-center font-mono text-xs text-ink-min">
          No bills match these filters.
        </p>
      ) : (
        <>
          <p className="mb-3 font-mono text-xs text-ink-min">
            Showing {bills.length.toLocaleString()} of {total.toLocaleString()}, latest action first
          </p>
          <div className="border border-white/[0.07] divide-y divide-white/[0.07]">
            {bills.map((bill) => (
              <BillRow key={`${bill.chamber}-${bill.billId}-${bill.sponsorId}`} bill={bill} />
            ))}
          </div>
          {current.error && (
            <p role="status" className="mt-4 text-center font-mono text-xs text-signal-red">
              More bills could not be loaded. Try again in a moment.
            </p>
          )}
          {current.page < current.totalPages && (
            <div className="mt-6 flex justify-center">
              <button
                type="button"
                onClick={() => setWant({ sig, page: current.page + 1 })}
                disabled={page > current.page}
                className="min-h-6 border border-white/[0.07] px-4 py-2 font-mono text-xs text-ink-lo transition-colors hover:border-white/15 hover:text-phos disabled:cursor-wait disabled:opacity-40"
              >
                {page > current.page
                  ? "Loading…"
                  : `Load more (${(total - bills.length).toLocaleString()} remaining)`}
              </button>
            </div>
          )}
        </>
      )}
    </section>
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
      <div className="text-center py-16 font-mono text-xs text-ink-min pulse-subtle">
        Loading...
      </div>
    );
  }
  if (stageTotals !== "error" && ALL_STAGE_CODES.every((code) => !(stageTotals[code] > 0))) {
    return <div className="text-center py-16 font-mono text-xs text-ink-min">No results</div>;
  }

  // A search opens every stage its matches sit at, so they are on screen
  // without a click per stage. It used to open a stage only when all the
  // matches sat at one (a bill number, nearly always): while a search had to
  // match a title as one unbroken string it seldom found more, but matched
  // word by word "student loans" finds 34 bills at five stages, and a
  // reader saw five closed headers instead of the bills.
  const matchingStages =
    q && stageTotals !== "error" ? ALL_STAGE_CODES.filter((code) => stageTotals[code] > 0) : [];

  return (
    <div className="flex flex-col gap-2">
      {ALL_STAGE_CODES.map((code) => (
        <BillStageGroup
          key={code}
          stageCode={code}
          forceExpanded={matchingStages.includes(code)}
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
    shown: PaginatedBills["shown"];
    movedSince: string | null;
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
          shown: res.shown ?? null,
          movedSince: res.movedSince ?? null,
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
          shown: prev?.sig === filterSig ? prev.shown : null,
          movedSince: prev?.sig === filterSig ? prev.movedSince : null,
          error: err instanceof Error ? err.message : "Failed to load bills",
        }));
      });

    return () => {
      cancelled = true;
    };
  }, [stage, chamber, party, q, page, filterSig, loaded]);

  if (loading) {
    return (
      <div className="text-center py-16 font-mono text-xs text-ink-min pulse-subtle">
        Loading...
      </div>
    );
  }
  if (error) {
    return <div className="text-center py-16 font-mono text-xs text-signal-red">{error}</div>;
  }
  const moved = current?.shown === "moved";
  const since = current?.movedSince ? shortDay(current.movedSince) : "a week ago";
  if (results.length === 0) {
    return (
      <div className="text-center py-16 font-mono text-xs text-ink-min space-y-3">
        <p>
          No action center issue names a bill, and none moved past referral since {since} for this
          filter
        </p>
        <button onClick={onViewAll} className="font-mono text-xs text-signal-cyan hover:underline">
          View all bills instead
        </button>
      </div>
    );
  }

  return (
    <>
      {/* Which list this is: most days no live issue names a bill, and the
          backend then serves what moved this week (bill_service). */}
      <p className="mb-2 max-w-3xl text-sm leading-relaxed text-ink-lo">
        {moved
          ? `No Action Center issue names a bill right now, so this lists bills that moved this week: a recorded action since ${since}, past referral to committee, latest first.`
          : "Bills named in today's Action Center issues, most-named first."}
      </p>
      <p className="font-mono text-xs text-ink-min mb-3">
        Showing {results.length.toLocaleString()} of {total.toLocaleString()} bill
        {total !== 1 ? "s" : ""}
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
            className="font-mono text-xs px-4 py-2 border border-white/[0.07] text-ink-lo hover:text-phos hover:border-white/15 disabled:opacity-40 disabled:cursor-wait transition-colors"
          >
            {loadingMore
              ? "Loading..."
              : `Load more (${(total - results.length).toLocaleString()} remaining)`}
          </button>
        </div>
      )}
    </>
  );
}
