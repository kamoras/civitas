"use client";

import Link from "next/link";
import type {
  ExploreAgencyMatch,
  ExploreBillMatch,
  ExploreMatches,
  ExploreMemberMatch,
} from "@/lib/api";
import { useConfig } from "@/hooks/useConfig";
import { PARTY_BADGE } from "@/lib/partyStyles";
import { billStageStyle } from "@/lib/billStages";
import { billHref } from "@/lib/congress";
import { agencyHref } from "@/lib/routes";
import Photo from "@/components/Photo";

/**
 * The members, bills and agencies a search names, as their own groups above Explore's
 * document results (GET /api/explore/matches). Explore searches documents,
 * so a senator's surname listed thirty floor speeches and no link to the senator,
 * and "flag burning" never offered the Flag Burners Act. Grouped, never
 * merged into the document ranking: each group is the backend's own list,
 * names and titles verbatim.
 */
export default function MatchGroups({
  matches,
  query,
}: {
  matches: ExploreMatches;
  query: string;
}) {
  const { members, bills } = matches;
  const agencies = matches.agencies ?? { total: 0, items: [] };
  const groups = [members, bills, agencies].filter((g) => g.total > 0).length;
  if (!groups) return null;
  return (
    <div className={`mb-4 grid gap-3 ${groups > 1 ? "sm:grid-cols-2" : ""}`}>
      {members.total > 0 && (
        <Group id="match-members" label="Members" count={members.total}>
          {members.items.map((m) => (
            <MemberRow key={m.id} m={m} />
          ))}
        </Group>
      )}
      {bills.total > 0 && (
        <Group
          id="match-bills"
          label="Bills"
          count={bills.total}
          more={
            bills.total > bills.items.length
              ? {
                  href: `/congress/bills?q=${encodeURIComponent(query)}`,
                  text: `All ${bills.total} bills matching “${query}”`,
                }
              : null
          }
        >
          {bills.items.map((b) => (
            <BillRow key={`${b.billId}-${b.congress}`} b={b} />
          ))}
        </Group>
      )}
      {agencies.total > 0 && (
        <Group
          id="match-agencies"
          label="Agencies"
          count={agencies.total}
          more={
            agencies.total > agencies.items.length
              ? {
                  href: `/agencies?q=${encodeURIComponent(query)}`,
                  text: `Agencies named “${query}”`,
                }
              : null
          }
        >
          {agencies.items.map((a) => (
            <AgencyRow key={a.slug} a={a} />
          ))}
        </Group>
      )}
    </div>
  );
}

function Group({
  id,
  label,
  count,
  more = null,
  children,
}: {
  id: string;
  label: string;
  count: number;
  more?: { href: string; text: string } | null;
  children: React.ReactNode;
}) {
  return (
    <section aria-labelledby={id} className="border border-white/15 bg-surface-raised/40 px-4 py-3">
      <h2
        id={id}
        className="mb-2 flex items-baseline gap-2 font-mono text-xs uppercase tracking-[0.12em]"
      >
        <span className="text-phos">{label}</span>{" "}
        <span className="text-ink-min">
          {count} match{count === 1 ? "" : "es"}
        </span>
      </h2>
      <ul className="flex flex-col">{children}</ul>
      {more && (
        <Link
          href={more.href}
          className="mt-2 inline-flex min-h-6 items-center text-xs text-ink-lo underline decoration-white/20 underline-offset-2 hover:text-phos"
        >
          {more.text} →
        </Link>
      )}
    </section>
  );
}

function MemberRow({ m }: { m: ExploreMemberMatch }) {
  const party = PARTY_BADGE[m.party] ?? PARTY_BADGE.I;
  const seat =
    m.chamber === "senate" ? `Senator · ${m.state}` : `Representative · ${m.state}-${m.district}`;
  return (
    <li>
      <Link
        href={`/politicians/${m.id}`}
        className="flex min-h-11 items-center gap-2.5 border-b border-white/[0.06] py-1.5 text-sm text-ink-hi hover:text-phos"
      >
        {m.thumbnailUrl ? (
          <Photo
            src={m.thumbnailUrl}
            alt=""
            lazy
            sizes="28px"
            className="h-7 w-7 shrink-0 border border-white/[0.07] object-cover object-[center_25%]"
          />
        ) : (
          <span className="h-7 w-7 shrink-0 border border-white/[0.07]" aria-hidden="true" />
        )}
        <span className="min-w-0 flex-1 truncate">{m.name}</span>{" "}
        <span className={`shrink-0 border px-1 font-mono text-xs ${party.className}`}>
          {party.label}
        </span>{" "}
        <span className="shrink-0 font-mono text-xs text-ink-lo">{seat}</span>
      </Link>
    </li>
  );
}

function BillRow({ b }: { b: ExploreBillMatch }) {
  const config = useConfig();
  const stage = b.stage ? billStageStyle(b.stage) : null;
  const stageName = b.stage ? (config?.billStages?.[b.stage]?.name ?? b.stage) : null;
  const name = b.shortTitle || b.title;
  return (
    <li>
      <Link
        href={billHref(b.billId, b.congress)}
        className="flex min-h-11 items-start gap-2.5 border-b border-white/[0.06] py-1.5 text-sm hover:text-phos"
      >
        <span className="w-20 shrink-0 pt-px font-mono text-xs text-phos">{b.label}</span>{" "}
        <span className="min-w-0 flex-1">
          <span className="line-clamp-2 text-ink-hi">{name ?? "Open the bill's page"}</span>
          {stage && stageName && (
            <span
              className={`mt-1 inline-block border px-1.5 font-mono text-xs uppercase tracking-wider ${stage.text} ${stage.border} ${stage.bg}`}
            >
              {stageName}
            </span>
          )}
        </span>
      </Link>
    </li>
  );
}

function AgencyRow({ a }: { a: ExploreAgencyMatch }) {
  return (
    <li>
      <Link
        href={agencyHref(a.slug)}
        className="flex min-h-11 items-center gap-2.5 border-b border-white/[0.06] py-1.5 text-sm text-ink-hi hover:text-phos"
      >
        <span className="min-w-0 flex-1 truncate">{a.name}</span>
        {a.shortName && (
          <span className="shrink-0 font-mono text-xs text-ink-lo">{a.shortName}</span>
        )}
      </Link>
    </li>
  );
}
