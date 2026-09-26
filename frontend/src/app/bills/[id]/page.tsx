import { Metadata } from "next";
import { notFound } from "next/navigation";
import type { BillDetail } from "@/types/bill";
import { usableRecord } from "@/lib/ssrPayload";
import { absoluteUrl, pageMetadata } from "@/lib/site";
import { describeBill, legislationJsonLd } from "@/lib/seo";
import JsonLd, { breadcrumbList } from "@/components/seo/JsonLd";
import BillDetailClient from "./BillDetailClient";

const BACKEND = process.env.BACKEND_URL || "http://backend:8000";

async function fetchBill(id: string): Promise<BillDetail | null> {
  try {
    const res = await fetch(`${BACKEND}/api/bills/${encodeURIComponent(id)}`, {
      next: { revalidate: 120 },
    });
    if (!res.ok) return null;
    return usableRecord<BillDetail>(await res.json(), "billId", "title");
  } catch {
    return null;
  }
}

export async function generateMetadata({
  params,
}: {
  params: Promise<{ id: string }>;
}): Promise<Metadata> {
  const { id } = await params;
  const bill = await fetchBill(id);

  if (!bill) {
    return pageMetadata({
      title: "Bill not found",
      description: "No record for this bill.",
      path: `/bills/${encodeURIComponent(id)}`,
      noindex: true,
    });
  }
  // Canonical from the record, not the request: "/bills/s.1" and
  // "/bills/S.1" resolve to the same bill and must not index as two pages.
  return pageMetadata({ ...describeBill(bill), path: `/bills/${encodeURIComponent(bill.billId)}`, type: "article" });
}

export default async function BillDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const bill = await fetchBill(id);

  if (!bill) notFound();

  return (
    <>
      <JsonLd
        data={[
          legislationJsonLd(bill),
          breadcrumbList([
            { name: "Home", url: absoluteUrl("/") },
            { name: "Bills", url: absoluteUrl("/bills") },
            { name: bill.billId, url: absoluteUrl(`/bills/${encodeURIComponent(bill.billId)}`) },
          ]),
        ]}
      />
      <BillDetailClient bill={bill} />
    </>
  );
}
