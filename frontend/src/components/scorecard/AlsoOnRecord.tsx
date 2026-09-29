"use client";

import { useEffect, useState } from "react";
import { fetchRepStockTrades, fetchSenatorStockTrades } from "@/lib/api";
import { countLobbiedBills } from "@/lib/lobbying";
import type { Senator } from "@/types/senator";

export type RecordView = "trades" | "donorVotes" | "positions";

function Tile({
  title,
  line,
  meta,
  onOpen,
}: {
  title: string;
  line: string;
  meta: string;
  onOpen?: () => void;
}) {
  const body = (
    <>
      <span className="text-base font-bold text-ink-hi">{title}</span>
      <span className="text-sm text-ink-lo">{line}</span>
      <span className="font-mono text-xs text-ink-min">{meta}</span>
    </>
  );
  const box =
    "flex min-h-[96px] flex-col gap-1.5 border border-white/[0.18] bg-surface px-4 py-4 text-left";
  // Nothing to open onto: a plain box, not a button that does nothing.
  if (!onOpen) return <div className={box}>{body}</div>;
  return (
    <button type="button" onClick={onOpen} className={`${box} hover:border-white/40`}>
      {body}
    </button>
  );
}

/** What is on record about the member but not scored, one line each; a
 *  tile with something behind it opens it in the drawer. Counts are the
 *  API's (the trades endpoint's total and late count, the stored
 *  donor-vote overlaps with the bills their lobbying filings name, and
 *  policy positions). */
export default function AlsoOnRecord({
  member,
  chamber,
  onOpen,
}: {
  member: Senator;
  chamber: "senate" | "house";
  onOpen: (view: RecordView) => void;
}) {
  // undefined while loading; null when the count couldn't be read.
  const [trades, setTrades] = useState<{ total: number; late: number } | null | undefined>(
    undefined
  );

  useEffect(() => {
    let live = true;
    const fetcher = chamber === "house" ? fetchRepStockTrades : fetchSenatorStockTrades;
    fetcher(member.id, { page: 1, perPage: 1 })
      .then((r) => live && setTrades({ total: r.total, late: r.lateCount }))
      .catch(() => live && setTrades(null));
    return () => {
      live = false;
    };
  }, [member.id, chamber]);

  const overlaps = member.lobbyingMatches?.length ?? 0;
  const lobbied = countLobbiedBills(member.lobbyingMatches ?? []);
  const positions = member.partisanDepth?.totalPositions ?? 0;

  return (
    <section className="flex flex-col gap-3" aria-labelledby="also-on-record">
      <h2
        id="also-on-record"
        className="font-mono text-xs uppercase tracking-[0.14em] text-ink-min"
      >
        Also on record, not part of the score
      </h2>
      <div className="grid gap-4 sm:grid-cols-3">
        <Tile
          title="Stock trades"
          line={
            trades === undefined
              ? "Loading…"
              : trades === null
                ? "Could not load the count"
                : trades.total === 0
                  ? "None disclosed"
                  : `${trades.total} trade${trades.total !== 1 ? "s" : ""} disclosed${trades.late > 0 ? `, ${trades.late} late` : ""}`
          }
          meta="STOCK Act periodic transaction reports"
          onOpen={trades && trades.total > 0 ? () => onOpen("trades") : undefined}
        />
        <Tile
          title="Donor and vote links"
          line={
            overlaps === 0
              ? "None found in tracked votes"
              : `${overlaps} donor-vote overlap${overlaps !== 1 ? "s" : ""}${
                  lobbied > 0
                    ? ` · ${lobbied} bill${lobbied !== 1 ? "s" : ""} named in lobbying filings under donors' names`
                    : ""
                }`
          }
          meta="Donor industries beside votes cast; LDA lobbying filings"
          onOpen={overlaps > 0 ? () => onOpen("donorVotes") : undefined}
        />
        <Tile
          title="Positions by policy area"
          line={
            positions === 0
              ? "Not enough votes to place yet"
              : `${positions} position${positions !== 1 ? "s" : ""} placed on the party line`
          }
          meta="Read from party-labeled votes"
          onOpen={positions > 0 ? () => onOpen("positions") : undefined}
        />
      </div>
    </section>
  );
}
