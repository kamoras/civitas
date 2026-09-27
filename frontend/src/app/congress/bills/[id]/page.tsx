import { Metadata } from "next";
import { notFound } from "next/navigation";
import type { BillDetail } from "@/types/bill";
import type { BillRecord } from "@/types/congress";
import { usableRecord } from "@/lib/ssrPayload";
import { absoluteUrl, pageMetadata } from "@/lib/site";
import { describeBill, legislationJsonLd } from "@/lib/seo";
import { fetchBillRecord } from "@/lib/congressServer";
import JsonLd, { breadcrumbList } from "@/components/seo/JsonLd";
import BillPageView from "@/components/congress/BillPageView";

const BACKEND = process.env.BACKEND_URL || "http://backend:8000";

/** The site's own record of a bill a current member sponsored (stage,
 * policy areas, Action Center mentions); null for any other bill, which
 * the Congress.gov record still covers. */
async function fetchTrackedBill(id: string): Promise<BillDetail | null> {
  try {
    const res = await fetch(`${BACKEND}/api/bills/${encodeURIComponent(id)}`, { next: { revalidate: 120 } });
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

async function load(id: string): Promise<{ record: BillRecord | null; detail: BillDetail | null }> {
  const [record, detail] = await Promise.all([fetchBillRecord(id), fetchTrackedBill(id)]);
  return { record, detail };
}

export async function generateMetadata({ params }: { params: Promise<{ id: string }> }): Promise<Metadata> {
  const { id } = await params;
  const { record, detail } = await load(id);
  if (!record && !detail) {
    return pageMetadata({ title: "Bill not found", description: "No record for this bill.", path: `/congress/bills/${encodeURIComponent(id)}`, noindex: true });
  }
  const billId = record?.billId ?? detail!.billId;
  // Canonical from the record, not the request: "/congress/bills/s.1" and
  // "/congress/bills/S.1" resolve to the same bill and must not index as two pages.
  if (detail) {
    return pageMetadata({ ...describeBill(detail), path: `/congress/bills/${encodeURIComponent(billId)}`, type: "article" });
  }
  return pageMetadata({
    title: `${record!.billLabel ?? billId}: ${record!.title ?? ""}`.trim(),
    description: record!.summary?.paragraphs[1] ?? record!.latestAction?.text ?? `${record!.billLabel} in the ${record!.congress}th Congress.`,
    path: `/congress/bills/${encodeURIComponent(billId)}`,
    type: "article",
  });
}

export default async function BillPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const [{ record, detail }, stages] = await Promise.all([load(id), fetchStageNames()]);
  if (!record && !detail) notFound();
  const billId = record?.billId ?? detail!.billId;
  return (
    <>
      <JsonLd
        data={[
          ...(detail ? [legislationJsonLd(detail)] : []),
          breadcrumbList([
            { name: "Home", url: absoluteUrl("/") },
            { name: "Congress", url: absoluteUrl("/congress") },
            { name: "Bills", url: absoluteUrl("/congress/bills") },
            { name: billId, url: absoluteUrl(`/congress/bills/${encodeURIComponent(billId)}`) },
          ]),
        ]}
      />
      <BillPageView billId={billId} record={record} detail={detail} stageName={detail ? stages[detail.stage]?.name ?? null : null} />
    </>
  );
}
