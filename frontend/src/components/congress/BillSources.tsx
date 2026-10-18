import type { ReactNode } from "react";
import Link from "next/link";
import type { BillDetail } from "@/types/bill";
import type { BillRecord } from "@/types/congress";
import CiteThis from "@/components/shared/CiteThis";
import { congressGovCitedSource } from "@/lib/citedSources";
import { longDate } from "@/lib/congress";
import { ordinal } from "@/components/scorecard/format";
import { onThisPage } from "@/lib/onThisPage";

const LINK = "text-ink underline decoration-white/30 underline-offset-4 hover:text-phos";

/**
 * The bill page's expert layer: where each part of the page comes from and
 * as of when, the record as data, and a reference to cite. The sources are
 * the ones the page reads (api/bills.py and bill_service.py); the dates are
 * the record's own.
 */
export default function BillSources({
  billId,
  label,
  title,
  congress,
  canonicalPath,
  record,
  detail,
}: {
  billId: string;
  label: string;
  title: string;
  congress: number | null;
  canonicalPath: string;
  record: BillRecord | null;
  detail: BillDetail | null;
}) {
  const votesJson = `/api/public/v1/bills/${encodeURIComponent(billId)}/votes${congress ? `?congress=${congress}` : ""}`;
  const [type, number] = billId.toLowerCase().split(".");
  const sources: [string, ReactNode][] = [
    [
      "Bill record",
      <>
        Congress.gov: the bill, its actions, cosponsors and text versions, read from the{" "}
        <a href="https://api.congress.gov/" className={LINK}>
          Congress.gov API
        </a>{" "}
        and kept for up to six hours
        {record?.latestAction?.actionDate
          ? `; latest action ${longDate(record.latestAction.actionDate)}`
          : ""}
        .
      </>,
    ],
    [
      "Summary",
      record?.summary
        ? `Congressional Research Service, ${record.summary.actionDesc}, ${longDate(record.summary.actionDate)}, quoted as published.`
        : // Unknown is not none: a record that couldn't be read says so.
          !record || record.unavailable.includes("summaries")
          ? "Not read from Congress.gov just now."
          : "None published by the Congressional Research Service yet.",
    ],
    [
      "Votes",
      record?.coverage?.votes.held === false
        ? "Not held for this Congress: each roll call is at Senate.gov or the House Clerk."
        : "Each roll call as Senate.gov or the House Clerk records it, stored by Civitas with every member's position.",
    ],
  ];
  if (detail) {
    sources.push([
      "Stage",
      <>
        Civitas&apos;s reading of the action history, the stages{" "}
        <Link href="/about/scores#effectiveness" className={LINK}>
          Legislative Effectiveness
        </Link>{" "}
        scores, counted over the bills sitting members sponsored.
      </>,
    ]);
  }
  return (
    <section
      aria-labelledby="sources"
      className="flex flex-col gap-6 border border-white/25 bg-surface p-5"
    >
      <h2
        id="sources"
        {...onThisPage("Data and citation")}
        className="font-display text-2xl font-extrabold text-ink-hi"
      >
        Sources, data and citation
      </h2>
      <dl className="grid grid-cols-1 gap-x-4 gap-y-2 text-sm sm:grid-cols-[7rem_minmax(0,1fr)]">
        {sources.map(([term, text]) => (
          <div key={term} className="contents">
            <dt className="font-mono text-xs uppercase tracking-[0.12em] text-ink-min">{term}</dt>
            <dd className="leading-relaxed text-ink-lo">{text}</dd>
          </div>
        ))}
      </dl>
      <div className="grid grid-cols-1 gap-8 lg:grid-cols-2">
        <div className="flex flex-col gap-2">
          <h3 className="font-mono text-xs uppercase tracking-[0.14em] text-ink-min">
            The record as data
          </h3>
          <ul className="flex flex-col text-sm">
            <li className="border-b border-white/[0.07] py-1.5">
              <a href={votesJson} className={LINK}>
                Every roll call on {label}, with each party&apos;s split (JSON)
              </a>
            </li>
            {congress && type && number && (
              <li className="border-b border-white/[0.07] py-1.5 text-ink-lo">
                Congress.gov API:{" "}
                <code className="break-all font-mono text-xs text-ink-hi">
                  api.congress.gov/v3/bill/{congress}/{type}/{number}
                </code>{" "}
                (a free api.data.gov key)
              </li>
            )}
            <li className="py-1.5 text-ink-lo">
              <Link href="/developers" className={LINK}>
                API reference and MCP server
              </Link>{" "}
              ·{" "}
              <Link href="/about/data" className="underline underline-offset-2 hover:text-phos">
                every data source
              </Link>
            </li>
          </ul>
        </div>
        <div className="flex flex-col gap-2">
          <h3 className="font-mono text-xs uppercase tracking-[0.14em] text-ink-min">
            Cite this bill page
          </h3>
          <CiteThis
            title={`${label}${title && title !== label ? `: ${title}` : ""}${congress ? ` (${ordinal(congress)} Congress)` : ""}`}
            date={record?.introducedDate ?? detail?.introducedDate ?? null}
            path={canonicalPath}
            source={congressGovCitedSource("U.S. Congress", record?.congressGovUrl)}
          />
        </div>
      </div>
    </section>
  );
}
