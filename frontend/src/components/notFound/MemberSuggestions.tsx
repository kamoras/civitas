"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { fetchPoliticianSuggestions, type PoliticianSuggestion } from "@/lib/api";
import { PARTY_COLORS } from "@/lib/partyStyles";

/**
 * On a member page's 404: the politicians whose addresses are closest to
 * the one asked for (backend `/politicians/suggest`), each a link, so a
 * mistyped name or a surname alone is one tap from the member. The address
 * is read in the browser because a not-found page gets no route params;
 * nothing about the reader is sent, only the path they asked for.
 */
export default function MemberSuggestions() {
  const pathname = usePathname();
  const asked = decodeURIComponent(pathname.split("/")[2] ?? "");
  const [found, setFound] = useState<PoliticianSuggestion[] | null>(null);

  useEffect(() => {
    if (asked.length < 2) return;
    let cancelled = false;
    fetchPoliticianSuggestions(asked.slice(0, 80))
      .then((s) => !cancelled && setFound(s))
      .catch(() => !cancelled && setFound([]));
    return () => {
      cancelled = true;
    };
  }, [asked]);

  if (!found?.length) return null;
  return (
    <div className="mt-5 text-left">
      <h2 className="font-mono text-xs uppercase tracking-[0.14em] text-ink-min">
        Closest names on the record
      </h2>
      <ul className="mt-2 flex flex-col gap-2">
        {found.map((p) => (
          <li key={p.id}>
            <Link
              href={`/politicians/${p.id}`}
              className="group flex min-h-11 flex-wrap items-baseline justify-between gap-x-3 border border-white/15 px-3 py-2 transition-colors hover:border-phos/60"
            >
              <span className="font-sans text-[15px] text-ink-hi group-hover:text-phos">
                {p.name}
              </span>
              <span className="font-mono text-xs text-ink-lo">
                {p.role}
                {p.party && p.state && (
                  <span className={PARTY_COLORS[p.party] ?? "text-ink-lo"}>
                    {" "}
                    · {p.party}-{p.state}
                  </span>
                )}
              </span>
            </Link>
          </li>
        ))}
      </ul>
    </div>
  );
}
