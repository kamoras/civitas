"use client";

import Form from "next/form";
import { useRef, useState } from "react";
import type { ExploreStats } from "@/lib/api";
import { EYEBROW } from "@/lib/typeStyles";
import { BRANCH_FILTERS, type DocumentQuery } from "@/lib/documentSearch";

const LEGEND = EYEBROW;
// A radio or checkbox drawn as a pill: the input itself is visually hidden
// but stays focusable; its label shows the state (inverted when checked)
// and the focus ring (WCAG 2.4.7).
const PILL =
  "inline-flex min-h-6 cursor-pointer items-center border border-white/15 px-3 py-1 text-xs text-ink-lo hover:text-phos peer-checked:border-ink-hi peer-checked:bg-ink-hi peer-checked:text-surface-base peer-focus-visible:outline peer-focus-visible:outline-2 peer-focus-visible:outline-offset-2 peer-focus-visible:outline-signal-cyan";
const SELECT =
  "min-h-11 w-full min-w-0 border border-white/15 bg-surface-base px-2 text-base text-ink-hi focus:border-signal-cyan/60 sm:text-sm";

/**
 * The Documents filter's controls: a GET form to /search, so each choice
 * is an address (a link, a refresh and Back all show it) and the list is
 * read by the server, with or without JavaScript. With it, a change
 * submits the form at once (next/form: a client navigation, no reload);
 * without it, the Apply button does. The words come from the page's search
 * box, carried here as a hidden field.
 */
export default function DocumentFilters({
  d,
  stats,
  agencyName,
}: {
  d: DocumentQuery;
  stats: ExploreStats | null;
  agencyName: string;
}) {
  const formRef = useRef<HTMLFormElement>(null);
  // The controls show the address's filters. A choice shows at once and
  // submits; when the address changes some other way (a link on the page),
  // the controls follow it (state derived from props, set during render).
  const [shown, setShown] = useState(d);
  const [from, setFrom] = useState(JSON.stringify(d));
  if (from !== JSON.stringify(d)) {
    setFrom(JSON.stringify(d));
    setShown(d);
  }
  const choose = (next: Partial<DocumentQuery>) => {
    setShown({ ...shown, ...next });
    // Submitted after the new state renders, so the form sends it.
    requestAnimationFrame(() => formRef.current?.requestSubmit());
  };
  const types = Object.entries(stats?.byType ?? {}).sort((a, b) => b[1] - a[1]);
  const agencies = stats?.agencies ?? [];

  return (
    <Form ref={formRef} action="/search" className="space-y-4">
      <input type="hidden" name="tab" value="documents" />
      {d.q && <input type="hidden" name="q" value={d.q} />}
      {d.politicianId && <input type="hidden" name="politician_id" value={d.politicianId} />}

      <fieldset>
        <legend className={LEGEND}>Branch</legend>
        <div className="mt-2 flex flex-wrap gap-2">
          {BRANCH_FILTERS.map((b) => (
            <label key={b.value || "all"} className="relative">
              <input
                type="radio"
                name="chamber"
                value={b.value}
                checked={shown.chamber === b.value}
                onChange={() => choose({ chamber: b.value })}
                className="peer sr-only"
              />
              <span className={PILL}>{b.label}</span>
            </label>
          ))}
        </div>
      </fieldset>

      <div className="grid gap-3 sm:grid-cols-2">
        {types.length > 0 && (
          <label className="flex flex-col gap-1 text-xs text-ink-lo">
            <span className={LEGEND}>Type</span>
            <select
              name="type"
              value={shown.type}
              onChange={(e) => choose({ type: e.target.value })}
              className={SELECT}
            >
              <option value="">Every type</option>
              {types.map(([t, n]) => (
                <option key={t} value={t}>
                  {t} ({n.toLocaleString("en-US")})
                </option>
              ))}
            </select>
          </label>
        )}
        {(agencies.length > 0 || d.agency) && (
          <label className="flex flex-col gap-1 text-xs text-ink-lo">
            <span className={LEGEND}>Agency</span>
            <select
              name="agency"
              value={shown.agency}
              onChange={(e) => choose({ agency: e.target.value })}
              className={SELECT}
            >
              <option value="">Every agency</option>
              {d.agency && !agencies.some((a) => a.slug === d.agency) && (
                <option value={d.agency}>{agencyName}</option>
              )}
              {agencies.map((a) => (
                <option key={a.slug} value={a.slug}>
                  {"  ".repeat(a.depth)}
                  {a.name} ({a.documents.toLocaleString("en-US")})
                </option>
              ))}
            </select>
          </label>
        )}
      </div>

      <div className="flex flex-wrap items-center gap-x-6 gap-y-3">
        <fieldset className="flex flex-wrap items-center gap-2">
          <legend className={`${LEGEND} mb-2 w-full`}>Sort</legend>
          {(
            [
              ["relevance", "Relevance"],
              ["date", "Newest"],
            ] as const
          ).map(([value, label]) => (
            <label key={value} className="relative">
              <input
                type="radio"
                name="sort"
                value={value}
                checked={shown.sort === value}
                onChange={() => choose({ sort: value })}
                className="peer sr-only"
              />
              <span className={PILL}>{label}</span>
            </label>
          ))}
        </fieldset>
        <label className="relative self-end">
          <input
            type="checkbox"
            name="comment"
            value="open"
            checked={shown.commentOpen}
            onChange={(e) => choose({ commentOpen: e.target.checked })}
            className="peer sr-only"
          />
          <span className={PILL}>Open for public comment only</span>
        </label>
      </div>

      <noscript>
        <button
          type="submit"
          className="min-h-11 border border-white/25 px-4 font-mono text-xs text-ink-hi"
        >
          Apply filters
        </button>
      </noscript>
    </Form>
  );
}
