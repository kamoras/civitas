import { beforeEach, describe, expect, it, vi } from "vitest";
import type { Metadata } from "next";

// Each record page previews as its own record card (/api/og?vote=, bill=,
// explore=), never the site card: a shared roll call carries its tally.
const fetchRollCall = vi.hoisted(() => vi.fn());
const fetchBillRecord = vi.hoisted(() => vi.fn());
const fetchExploreDoc = vi.hoisted(() => vi.fn());
const fetchRecord = vi.hoisted(() => vi.fn());
vi.mock("@/lib/congressServer", () => ({
  fetchRollCall,
  fetchBillRecord,
  fetchBillCommitteeRecord: vi.fn(),
}));
vi.mock("@/app/explore/[id]/fetchDoc", () => ({ fetchExploreDoc }));
vi.mock("@/lib/ssrPayload", () => ({ fetchRecord }));
vi.mock("@/app/explore/[id]/ExploreDocumentView", () => ({ default: () => null }));
vi.mock("@/components/congress/RollCallView", () => ({ default: () => null }));
vi.mock("@/components/congress/BillPageView", () => ({ default: () => null }));

import { generateMetadata as voteMeta } from "@/app/congress/votes/[chamber]/[congress]/[session]/[number]/page";
import { generateMetadata as billMeta } from "@/app/congress/bills/[id]/page";
import { generateMetadata as exploreMeta } from "@/app/explore/[id]/page";

function images(meta: Metadata): string[] {
  const og = (meta.openGraph?.images ?? []) as { url: string }[];
  const tw = (meta.twitter?.images ?? []) as { url: string }[];
  expect(tw.map((i) => i.url)).toEqual(og.map((i) => i.url));
  return og.map((i) => i.url);
}

describe("record pages' og:image", () => {
  beforeEach(() => vi.clearAllMocks());

  it("a roll call shares as its vote card", async () => {
    fetchRollCall.mockResolvedValue({
      chamber: "house",
      congress: 119,
      session: 2,
      number: 314,
      date: "2026-09-16",
      title: "A bill",
      question: "On Passage",
      members: [],
    });
    const meta = await voteMeta({
      params: Promise.resolve({ chamber: "house", congress: "119", session: "2", number: "314" }),
    });
    expect(images(meta)).toEqual([
      expect.stringMatching(/\/api\/og\?vote=house\/119\/2\/314&v=\w+$/),
    ]);
  });

  it("a tracked bill shares as its bill card", async () => {
    fetchBillRecord.mockResolvedValue({ record: null, failed: false });
    fetchRecord.mockResolvedValue({ billId: "S.2403", congress: 119, title: "A bill" });
    const meta = await billMeta({
      params: Promise.resolve({ id: "S.2403" }),
      searchParams: Promise.resolve({}),
    });
    expect(images(meta)).toEqual([
      expect.stringMatching(/\/api\/og\?billPage=119\/S\.2403&v=\w+$/),
    ]);
  });

  it("a bill the site doesn't track shares as its bill card too, from Congress.gov", async () => {
    fetchBillRecord.mockResolvedValue({
      record: { billId: "S.9", billLabel: "S. 9", congress: 119, title: "A bill" },
      failed: false,
    });
    fetchRecord.mockResolvedValue(null);
    const meta = await billMeta({
      params: Promise.resolve({ id: "S.9" }),
      searchParams: Promise.resolve({}),
    });
    expect(images(meta)).toEqual([expect.stringMatching(/\/api\/og\?billPage=119\/S\.9&v=\w+$/)]);
  });

  it("an Explore document shares as its document card", async () => {
    fetchExploreDoc.mockResolvedValue({ id: 19799, title: "A rule", docType: "Rule" });
    const meta = await exploreMeta({
      params: Promise.resolve({ id: "19799" }),
      searchParams: Promise.resolve({}),
    });
    expect(images(meta)).toEqual([expect.stringMatching(/\/api\/og\?explore=19799&v=\w+$/)]);
  });
});
