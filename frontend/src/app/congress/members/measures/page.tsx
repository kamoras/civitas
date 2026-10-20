import type { Metadata } from "next";
import { pageMetadata } from "@/lib/site";
import { describeMeasures } from "@/lib/seo";
import { MEASURES_HREF } from "@/lib/routes";
import type { ChamberMeasures } from "@/types/peers";
import MeasuresClient from "./MeasuresClient";

const BACKEND = process.env.BACKEND_URL || "http://backend:8000";

export const metadata: Metadata = pageMetadata({ ...describeMeasures(), path: MEASURES_HREF });

async function fetchMeasures(chamber: "senate" | "house"): Promise<ChamberMeasures | null> {
  try {
    const res = await fetch(`${BACKEND}/api/congress/measures?chamber=${chamber}`, {
      next: { revalidate: 300 },
    });
    return res.ok ? ((await res.json()) as ChamberMeasures) : null;
  } catch {
    return null;
  }
}

export default async function MeasuresPage({
  searchParams,
}: {
  searchParams: Promise<{ chamber?: string }>;
}) {
  const chamber = (await searchParams).chamber === "house" ? "house" : "senate";
  const data = await fetchMeasures(chamber);
  return <MeasuresClient chamber={chamber} data={data} />;
}
