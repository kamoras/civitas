"use client";

import { useState } from "react";
import type { ElectionCalendar } from "@/types/specials";
import { calendarEntries, longDate, type CalendarEntry as Entry } from "@/lib/specials";

type Row = Entry & { group?: Entry[] };

/** With every state shown, one line per date and kind for the entries
 * every state has (a general's Tuesday lists each state's House and Senate
 * general, and dozens of states' deadlines), opened to list them; a single
 * state's view lists each. Specials and reporting deadlines stand alone. */
function groupByDay(entries: Entry[]): Row[] {
  const out: Row[] = [];
  const byKey = new Map<string, Row>();
  for (const e of entries) {
    if (!e.kind) {
      out.push(e);
      continue;
    }
    const key = `${e.date}-${e.tag}-${e.kind}-${e.passed}`;
    const row = byKey.get(key);
    if (row) {
      row.group!.push(e);
      continue;
    }
    const fresh: Row = { ...e, key: `group-${key}`, group: [e] };
    byKey.set(key, fresh);
    out.push(fresh);
  }
  for (const row of byKey.values()) {
    if (row.group!.length === 1) {
      Object.assign(row, row.group![0], { group: undefined });
      continue;
    }
    const codes = new Set(row.group!.map((g) => g.state));
    const n = codes.size - (codes.has("DC") ? 1 : 0);
    const where = `${n} state${n === 1 ? "" : "s"}${codes.has("DC") ? " and DC" : ""}`;
    row.text = `${row.kind} in ${where}`;
    if (row.tag === "DEADLINE")
      row.status = row.passed ? "PASSED" : `${row.group!.length} DEADLINES`;
    row.state = null;
  }
  return out;
}

function monthOf(iso: string): string {
  return new Date(`${iso.slice(0, 7)}-15T12:00:00Z`).toLocaleDateString("en-US", {
    month: "long",
    year: "numeric",
    timeZone: "UTC",
  });
}

/** The calendar, filtered by state in the browser over what the page
 * already has (AGENTS.md §8: a choice, never remembered). */
export default function CalendarList({ cal }: { cal: ElectionCalendar }) {
  const [state, setState] = useState("");
  const all = calendarEntries(cal);
  const states = Array.from(
    new Map(
      [...cal.elections, ...cal.voterDeadlines].map((e) => [e.state, e.stateName] as const)
    ).entries()
  ).sort((a, b) => a[1].localeCompare(b[1]));
  const shown = state ? all.filter((e) => e.state === state || e.state === null) : groupByDay(all);
  const ahead = shown.filter((e) => !e.passed);
  const passed = shown.filter((e) => e.passed);
  const months = new Map<string, Row[]>();
  for (const e of ahead) {
    const m = monthOf(e.date);
    months.set(m, [...(months.get(m) ?? []), e]);
  }

  const row = (e: Row) => (
    <li
      key={e.key}
      className="grid grid-cols-[5.5rem_1fr] gap-x-3 border-t border-white/[0.07] py-2 sm:grid-cols-[7rem_7rem_1fr_auto]"
    >
      <span className="font-mono text-xs tabular-nums text-ink-hi">
        {longDate(e.date).replace(/, \d{4}$/, "")}
      </span>
      <span className="font-mono text-[11px] tracking-[0.12em] text-ink-min">{e.tag}</span>
      <span className="col-span-2 text-sm text-ink-lo sm:col-span-1">
        {e.group ? (
          <details>
            <summary className="flex min-h-11 cursor-pointer items-start">{e.text}</summary>
            <ul className="space-y-1 pb-2">
              {e.group.map((g) => (
                <li key={g.key}>
                  {g.href ? (
                    <a href={g.href} className="inline-block py-1 hover:text-phos">
                      {g.text}
                    </a>
                  ) : (
                    g.text
                  )}
                </li>
              ))}
            </ul>
          </details>
        ) : e.href ? (
          <a href={e.href} className="inline-block py-1 hover:text-phos">
            {e.text}
          </a>
        ) : (
          e.text
        )}
      </span>
      <span className="col-span-2 font-mono text-[11px] tracking-[0.12em] text-ink-min sm:col-span-1 sm:text-right">
        {e.status}
      </span>
    </li>
  );

  return (
    <div>
      <label className="flex flex-wrap items-center gap-3 font-mono text-xs uppercase tracking-[0.14em] text-ink-min">
        State
        <select
          value={state}
          onChange={(ev) => setState(ev.target.value)}
          className="min-h-11 border border-white/15 bg-surface-base px-3 font-mono text-sm normal-case tracking-normal text-ink-hi"
        >
          <option value="">Every state</option>
          {states.map(([code, name]) => (
            <option key={code} value={code}>
              {name}
            </option>
          ))}
        </select>
      </label>
      {ahead.length === 0 && (
        <p className="mt-4 text-sm text-ink-lo">
          Nothing ahead on the calendar{state ? " for this state" : ""} in the year to{" "}
          {longDate(cal.window.to)}.
        </p>
      )}
      {Array.from(months.entries()).map(([month, entries]) => (
        <section key={month} className="mt-6">
          <h3 className="font-mono text-xs uppercase tracking-[0.16em] text-ink-min">{month}</h3>
          <ul className="mt-1">{entries.map(row)}</ul>
        </section>
      ))}
      {passed.length > 0 && (
        <details className="mt-6">
          <summary className="flex min-h-11 cursor-pointer items-center font-mono text-xs uppercase tracking-[0.16em] text-ink-min">
            Passed in the last month ({passed.length})
          </summary>
          <ul className="mt-1">{passed.map(row)}</ul>
        </details>
      )}
    </div>
  );
}
