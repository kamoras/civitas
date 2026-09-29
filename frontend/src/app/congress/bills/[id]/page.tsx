import { Metadata } from "next";
import { notFound } from "next/navigation";
import type { BillDetail } from "@/types/bill";
import type { BillRecord } from "@/types/congress";
import { usableRecord } from "@/lib/ssrPayload";
import { absoluteUrl, pageMetadata } from "@/lib/site";
import { describeBill, legislationJsonLd } from "@/lib/seo";
import { fetchBillRecord } from "@/lib/congressServer";
import { billCanonicalPath, billHref, parseCongressParam } from "@/lib/congress";
import { ordinal } from "@/components/scorecard/format";
import JsonLd, { breadcrumbList } from "@/components/seo/JsonLd";
import BillPageView from "@/components/congress/BillPageView";

const BACKEND = process.env.BACKEND_URL || "http://backend:8000";

/** The site's own record of a bill a current member sponsored (stage,
 * policy areas, Action Center mentions); null for any other bill, which
 * the Congress.gov record still covers. */
async function fetchTrackedBill(id: string, congress: number | null): Promise<BillDetail | null> {
  try {
    const query = congress ? `?congress=${congress}` : "";
    const res = await fetch(`${BACKEND}/api/bills/${encodeURIComponent(id)}${query}`, {
      next: { revalidate: 120 },
    });
    if (!res.ok) return null;
    return usableRecord<BillDetail>(await res.json(), "billId", "title");
  } catch {
    return null;
  }
}

async function fetchStageNames(): Promise<Record<string, { name: string }>> {
  try {
    const res = await fetch(`${BACKEND}/api/config`, { next: { revalidate: 3600 } });
    if (!res.ok) return {};
    return ((await res.json())?.billStages ?? {}) as Record<string, { name: string }>;
  } catch {
    return {};
  }
}

type PageProps = {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ congress?: string | string[] }>;
};

/** The bill `id` names in the Congress `?congress=` asks for (the current one
 * without it): a bill number is a different bill in each Congress. The
 * site's own record is used only when it is of the same Congress as the
 * Congress.gov record, so the page never mixes two bills. */
async function load(
  id: string,
  congress: number | null
): Promise<{ record: BillRecord | null; detail: BillDetail | null }> {
  const [record, tracked] = await Promise.all([
    fetchBillRecord(id, congress),
    fetchTrackedBill(id, congress),
  ]);
  const detail = tracked && record && tracked.congress !== record.congress ? null : tracked;
  return { record, detail };
}

export async function generateMetadata({ params, searchParams }: PageProps): Promise<Metadata> {
  const { id } = await params;
  const congress = parseCongressParam((await searchParams).congress);
  const { record, detail } = await load(id, congress);
  if (!record && !detail) {
    return pageMetadata({
      title: "Bill not found",
      description: "No record for this bill.",
      path: billHref(id, congress),
      noindex: true,
    });
  }
  const billId = record?.billId ?? detail!.billId;
  // Canonical from the record, not the request: "/congress/bills/s.1" and
  // "/congress/bills/S.1?congress=119" resolve to the same bill and must not
  // index as two pages; an earlier Congress's bill keeps its ?congress=.
  const path = billCanonicalPath(billId, record?.congress ?? detail!.congress);
  if (detail) {
    return pageMetadata({ ...describeBill(detail), path, type: "article" });
  }
  return pageMetadata({
    title: record!.title
      ? `${record!.billLabel ?? billId}: ${record!.title}`
      : (record!.billLabel ?? billId),
    description:
      record!.summary?.paragraphs[1] ??
      record!.latestAction?.text ??
      `${record!.billLabel} in the ${ordinal(record!.congress)} Congress.`,
    path,
    type: "article",
  });
}

export default async function BillPage({ params, searchParams }: PageProps) {
  const { id } = await params;
  const congress = parseCongressParam((await searchParams).congress);
  const [{ record, detail }, stages] = await Promise.all([load(id, congress), fetchStageNames()]);
  if (!record && !detail) notFound();
  const billId = record?.billId ?? detail!.billId;
  const canonical = billCanonicalPath(billId, record?.congress ?? detail!.congress);
  return (
    <>
      <JsonLd
        data={[
          ...(detail ? [legislationJsonLd(detail)] : []),
          breadcrumbList([
            { name: "Home", url: absoluteUrl("/") },
            { name: "Congress", url: absoluteUrl("/congress") },
            { name: "Bills", url: absoluteUrl("/congress/bills") },
            { name: billId, url: absoluteUrl(canonical) },
          ]),
        ]}
      />
      <BillPageView
        billId={billId}
        canonicalPath={canonical}
        record={record}
        detail={detail}
        stageName={detail ? (stages[detail.stage]?.name ?? null) : null}
      />
    </>
  );
}
