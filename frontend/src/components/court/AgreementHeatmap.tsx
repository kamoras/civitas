"use client";

import { useRef, useState, type KeyboardEvent } from "react";
import type { Court } from "@/types/court";
import ShareSectionButton from "@/components/share/ShareSectionButton";
import { SHARE_EXCLUDE_ATTR, SHARE_SECTION_ATTR } from "@/lib/shareImage";
import { heatColor, heatDomain, pairLookup, surname, termsPhrase } from "@/lib/court";
import { onThisPage } from "@/lib/onThisPage";

/**
 * How often each pair of sitting justices voted the same way: a grid in
 * Martin-Quinn order (so blocs show as blocks), each cell labelled with
 * its share, darker for more agreement. A data grid (WAI-ARIA grid
 * pattern): one tab stop, arrow keys move cell to cell, and the focused or
 * hovered cell's counts are stated below it. Every pair is also listed as
 * a plain table.
 */
export default function AgreementHeatmap({ court }: { court: Court }) {
  const justices = court.justices;
  const n = justices.length;
  const find = pairLookup(court.agreement);
  const domain = heatDomain(court.agreement);
  const [active, setActive] = useState<[number, number]>([0, Math.min(1, n - 1)]);
  const [hover, setHover] = useState<[number, number] | null>(null);
  const cells = useRef<(HTMLTableCellElement | null)[]>([]);

  if (n < 2 || court.agreement.length === 0) {
    return (
      <section
        id="agreement"
        {...onThisPage("Who votes with whom")}
        aria-labelledby="agreement-heading"
        className="border border-white/25 bg-surface px-4 py-5 sm:px-6"
      >
        <h2 id="agreement-heading" className="text-xl font-semibold text-ink-hi">
          Who votes with whom
        </h2>
        <p className="mt-2 text-base text-ink">
          No decided case with two sitting justices voting is stored yet.
        </p>
      </section>
    );
  }

  const describe = ([r, c]: [number, number]) => {
    const a = justices[r];
    const b = justices[c];
    if (r === c) return `${surname(a)}: the same justice`;
    const p = find(a.id, b.id);
    if (!p) return `${surname(a)} and ${surname(b)}: no case both decided`;
    return `${surname(a)} and ${surname(b)} voted the same way in ${p.agree} of the ${p.both} cases both decided (${Math.round(p.share)}%)`;
  };

  // A cell's name starts with the figure written in it (WCAG 2.5.3, label
  // in name), then says what it counts.
  const cellName = ([r, c]: [number, number]) => {
    const p = r === c ? null : find(justices[r].id, justices[c].id);
    return p ? `${Math.round(p.share)}%: ${describe([r, c])}` : describe([r, c]);
  };

  const move = (r: number, c: number) => {
    const next: [number, number] = [
      Math.max(0, Math.min(n - 1, r)),
      Math.max(0, Math.min(n - 1, c)),
    ];
    setActive(next);
    cells.current[next[0] * n + next[1]]?.focus();
  };

  const onKeyDown = (e: KeyboardEvent<HTMLTableElement>) => {
    const [r, c] = active;
    const keys: Record<string, () => void> = {
      ArrowRight: () => move(r, c + 1),
      ArrowLeft: () => move(r, c - 1),
      ArrowDown: () => move(r + 1, c),
      ArrowUp: () => move(r - 1, c),
      Home: () => (e.ctrlKey ? move(0, 0) : move(r, 0)),
      End: () => (e.ctrlKey ? move(n - 1, n - 1) : move(r, n - 1)),
    };
    const run = keys[e.key];
    if (run) {
      e.preventDefault();
      run();
    }
  };

  const shown = hover ?? active;
  const pairs = [...court.agreement].sort((x, y) => y.share - x.share || y.both - x.both);
  const name = new Map(justices.map((j) => [j.id, surname(j)]));

  return (
    <section
      id="agreement"
      {...onThisPage("Who votes with whom")}
      aria-labelledby="agreement-heading"
      {...{ [SHARE_SECTION_ATTR]: "agreement" }}
      className="flex flex-col gap-4 border border-white/25 bg-surface px-4 py-5 sm:px-6"
    >
      <div>
        <h2 id="agreement-heading" className="text-xl font-semibold text-ink-hi">
          Who votes with whom
        </h2>
        <p className="mt-1 font-mono text-xs text-ink-min">
          Share of cases both decided the same way · {termsPhrase(court.record.terms)}
        </p>
      </div>
      <p className="max-w-prose text-base leading-relaxed text-ink">
        Each square is one pair of justices: the percentage of the cases both took part in where
        they were on the same side. Justices are in the same order as the line above, so justices
        who vote alike sit in blocks.
      </p>

      <div className="night overflow-x-auto p-2">
        <table
          role="grid"
          aria-labelledby="agreement-heading"
          aria-describedby="agreement-detail"
          onKeyDown={onKeyDown}
          className="mx-auto border-separate border-spacing-0.5 font-mono text-xs"
        >
          <thead>
            <tr>
              <td />
              {justices.map((j) => (
                <th
                  key={j.id}
                  scope="col"
                  className="h-20 w-7 align-bottom font-normal text-ink-lo sm:w-11"
                >
                  <span className="inline-block max-h-20 rotate-180 whitespace-nowrap [writing-mode:vertical-rl]">
                    {surname(j)}
                  </span>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {justices.map((row, r) => (
              <tr key={row.id}>
                <th
                  scope="row"
                  className="whitespace-nowrap pr-2 text-right font-normal text-ink-lo"
                >
                  {surname(row)}
                </th>
                {justices.map((col, c) => {
                  const p = r === c ? null : find(row.id, col.id);
                  const isActive = active[0] === r && active[1] === c;
                  return (
                    <td
                      key={col.id}
                      ref={(el) => {
                        cells.current[r * n + c] = el;
                      }}
                      role="gridcell"
                      tabIndex={isActive ? 0 : -1}
                      aria-label={cellName([r, c])}
                      onFocus={() => setActive([r, c])}
                      onMouseEnter={() => setHover([r, c])}
                      onMouseLeave={() => setHover(null)}
                      onClick={() => move(r, c)}
                      className="h-7 w-7 cursor-default text-center tabular-nums text-ink-hi outline-none focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-signal-amber sm:h-11 sm:w-11 sm:text-sm"
                      style={{ background: p ? heatColor(p.share, domain) : "transparent" }}
                    >
                      {r === c ? (
                        <span className="text-ink-min" aria-hidden="true">
                          &middot;
                        </span>
                      ) : p ? (
                        Math.round(p.share)
                      ) : (
                        ""
                      )}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <p id="agreement-detail" aria-live="polite" className="min-h-12 text-sm text-ink">
        {describe(shown)}.
      </p>

      <div
        className="flex flex-wrap items-center gap-3 font-mono text-xs text-ink-lo"
        aria-hidden="true"
      >
        <span>{domain[0]}%</span>
        <span
          className="h-3 w-40"
          style={{
            background: `linear-gradient(to right, ${heatColor(domain[0], domain)}, ${heatColor(domain[1], domain)})`,
          }}
        />
        <span>{domain[1]}% of cases together</span>
      </div>

      <details className="text-sm" {...{ [SHARE_EXCLUDE_ATTR]: "" }}>
        <summary className="inline-flex min-h-6 cursor-pointer items-center font-mono text-xs text-ink-lo hover:text-phos">
          Every pair as a table
        </summary>
        <div className="mt-3 overflow-x-auto" tabIndex={0} role="region" aria-label="Every pair">
          <table className="w-full border-collapse">
            <caption className="sr-only">
              Agreement between each pair of sitting justices, most first
            </caption>
            <thead>
              <tr className="border-b border-white/15 text-left font-mono text-xs text-ink-min">
                <th scope="col" className="py-1.5 pr-3 font-normal">
                  Pair
                </th>
                <th scope="col" className="py-1.5 pr-3 text-right font-normal">
                  Same side
                </th>
                <th scope="col" className="py-1.5 pr-3 text-right font-normal">
                  Both decided
                </th>
                <th scope="col" className="py-1.5 text-right font-normal">
                  Share
                </th>
              </tr>
            </thead>
            <tbody>
              {pairs.map((p) => (
                <tr key={`${p.a}-${p.b}`} className="border-b border-white/[0.07]">
                  <th scope="row" className="py-1.5 pr-3 text-left font-normal text-ink">
                    {name.get(p.a)} and {name.get(p.b)}
                  </th>
                  <td className="py-1.5 pr-3 text-right font-mono tabular-nums">{p.agree}</td>
                  <td className="py-1.5 pr-3 text-right font-mono tabular-nums">{p.both}</td>
                  <td className="py-1.5 text-right font-mono tabular-nums text-ink-hi">
                    {p.share.toFixed(1)}%
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>

      <div className="flex justify-end">
        <ShareSectionButton label="Who votes with whom" />
      </div>
    </section>
  );
}
