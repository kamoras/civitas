import { Metadata } from "next";
import { notFound } from "next/navigation";
import type { BillDetail } from "@/types/bill";
import BillDetailClient from "./BillDetailClient";

const BACKEND = process.env.BACKEND_URL || "http://backend:8000";
const SITE = "https://civitas-research.org";

async function fetchBill(id: string): Promise<BillDetail | null> {
  try {
    const res = await fetch(`${BACKEND}/api/bills/${encodeURIComponent(id)}`, {
      next: { revalidate: 120 },
    });
    if (!res.ok) return null;
    return await res.json();
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

  const title = bill ? `${bill.billId} — ${bill.title} — Civitas` : "Bill — Civitas";
  const description = bill
    ? `${bill.billId}, sponsored by ${bill.sponsorName} (${bill.sponsorParty}-${bill.sponsorState}). ${bill.latestAction}`.trim()
    : "Bill detail on Civitas.";

  return {
    title,
    description,
    openGraph: {
      title,
      description,
      url: `${SITE}/bills/${id}`,
      siteName: "Civitas",
    },
  };
}

export default async function BillDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const bill = await fetchBill(id);

  if (!bill) notFound();

  return <BillDetailClient bill={bill} />;
}
