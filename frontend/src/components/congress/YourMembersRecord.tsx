"use client";

import { membersHref } from "@/lib/routes";
import Link from "next/link";
import { useEffect, useState } from "react";
import type { StateMemberRecord } from "@/types/congress";
import { useYourState } from "@/hooks/useYourState";
import { STATE_NAMES } from "@/lib/stateCodes";
import { PARTY_COLORS } from "@/lib/partyStyles";
import { billHref, rollCallHref, shortDate } from "@/lib/congress";
import Photo from "@/components/Photo";

const n = (x: number) => x.toLocaleString("en-US");
const LINK = "underline decoration-white/30 underline-offset-4 hover:text-phos";

function seat(m: StateMemberRecord) {
  if (m.chamber === "senate") return `Senator · ${m.state}`;
  return `Representative · ${m.state}-${m.district ? m.district : "AL"}`;
}

function MemberCard({ m }: { m: StateMemberRecord }) {
  return (
    <li className="flex min-w-0 flex-col gap-3 border border-white/[0.07] border-t-white/15 bg-surface p-4">
      <div className="flex items-center gap-3">
        {m.photo && (
          <Photo
            src={m.photo}
            alt=""
            sizes="48px"
            lazy
            className="h-12 w-12 shrink-0 object-cover object-[center_25%]"
          />
        )}
        <div className="min-w-0">
          <h3 className="font-display text-base font-semibold text-ink-hi">
            <Link href={`/politicians/${m.id}`} className="hover:text-phos">
              {m.name}
            </Link>{" "}
            <span className={`font-mono text-xs ${PARTY_COLORS[m.party] ?? "text-ink-lo"}`}>
              ({m.party})
            </span>
          </h3>
          <p className="font-mono text-xs uppercase tracking-[0.12em] text-ink-min">{seat(m)}</p>
        </div>
      </div>
      <p className="text-sm text-ink">
        Voted on <strong className="text-ink-hi">{n(m.voted)}</strong> of {n(m.chamberRollCalls)}{" "}
        roll calls this Congress, missed <strong className="text-ink-hi">{n(m.missed)}</strong>;
        sponsored <strong className="text-ink-hi">{n(m.billsSponsored)}</strong>{" "}
        {m.billsSponsored === 1 ? "bill" : "bills"}.
      </p>
      {m.votes.length > 0 && (
        <div>
          <h4 className="font-mono text-xs uppercase tracking-[0.12em] text-ink-min">
            Latest votes
          </h4>
          <ul className="mt-1 flex flex-col">
            {m.votes.map((v, i) => (
              <li key={i} className="border-t border-white/[0.07] py-1.5 text-sm text-ink">
                <span className="font-mono text-xs text-ink-min">{shortDate(v.date)}</span>{" "}
                <strong className="text-ink-hi">{v.vote}</strong>
                {v.votedWithParty !== null && (
                  <span className="text-ink-lo">
                    {v.votedWithParty ? ", with party" : ", against party"}
                  </span>
                )}
                {v.rollCall ? (
                  <>
                    {" · "}
                    <Link href={rollCallHref(v.rollCall)} className={LINK}>
                      {v.rollCall.question}
                    </Link>
                  </>
                ) : null}
                <span className="mt-0.5 line-clamp-2 block text-ink-lo">{v.billName}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
      {m.bills.length > 0 && (
        <div>
          <h4 className="font-mono text-xs uppercase tracking-[0.12em] text-ink-min">
            Latest bills
          </h4>
          <ul className="mt-1 flex flex-col">
            {m.bills.map((b) => (
              <li key={b.billId} className="border-t border-white/[0.07] py-1.5 text-sm text-ink">
                <Link href={billHref(b.billId, b.congress)} className={`font-mono ${LINK}`}>
                  {b.billLabel ?? b.billId}
                </Link>{" "}
                {b.title}
              </li>
            ))}
          </ul>
        </div>
      )}
      <Link href={`/politicians/${m.id}`} className={`mt-auto text-sm text-signal-cyan ${LINK}`}>
        {m.name}&apos;s full record
      </Link>
    </li>
  );
}

/**
 * /congress's "your members" panel, for the state picked in the header
 * this tab (useYourState, AGENTS.md section 8): each sitting member's roll
 * calls and missed votes this Congress, latest votes and bills, linking to
 * the profile. The state goes in the URL of the one request that loads
 * them and nowhere else. Nothing without a chosen state.
 */
export default function YourMembersRecord() {
  const { state, ready } = useYourState();
  const [loaded, setLoaded] = useState<{ state: string; members: StateMemberRecord[] } | null>(
    null
  );
  const [failedFor, setFailedFor] = useState<string | null>(null);
  useEffect(() => {
    if (!state) return;
    let live = true;
    fetch(`/api/congress/members?state=${state}`)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
      .then((body) => live && setLoaded(body))
      .catch(() => live && setFailedFor(state));
    return () => {
      live = false;
    };
  }, [state]);
  if (!ready || !state) return null;
  const name = STATE_NAMES[state] ?? state;
  const members = loaded?.state === state ? loaded.members : null;
  const failed = failedFor === state && !members;
  return (
    <section aria-labelledby="your-members-heading" className="mb-8">
      <h2
        id="your-members-heading"
        className="border-b border-white/15 pb-2 font-mono text-xs uppercase tracking-[0.16em] text-ink-min"
      >
        Your members this Congress · {name}
      </h2>
      {failed ? (
        <p className="mt-3 text-sm text-ink-lo">
          {name}&apos;s members couldn&apos;t be loaded just now.{" "}
          <Link href={membersHref(state)} className={LINK}>
            Find them in the directory
          </Link>
        </p>
      ) : !members ? (
        <p className="mt-3 text-sm text-ink-min" aria-live="polite">
          Loading {name}&apos;s members…
        </p>
      ) : (
        <ul className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {members.map((m) => (
            <MemberCard key={m.id} m={m} />
          ))}
        </ul>
      )}
    </section>
  );
}
