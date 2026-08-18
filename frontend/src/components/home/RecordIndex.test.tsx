import { describe, expect, it } from "vitest";
import { buildRecordEntries, formatEntryDate } from "./RecordIndex";
import type { NationalMonitor } from "@/lib/api";
import type { ActionIssue } from "@/types/action";
import type { BillInFlight } from "@/types/bill";

function issue(o: Partial<ActionIssue> = {}): ActionIssue {
  return {
    id: 7,
    date: "2026-08-18",
    rank: 1,
    title: "Prescription drug pricing",
    summary: "A summary of the issue.",
    facts: [],
    actions: [],
    sourceUrls: [],
    sourceNames: [],
    policyAreas: ["Health"],
    relatedBills: [],
    relatedExploreDocs: [],
    relatedSenators: [],
    concernedCount: 0,
    notPriorityCount: 0,
    ...o,
  };
}

function monitor(o: Partial<NationalMonitor> = {}): NationalMonitor {
  return {
    id: 3,
    slug: "appropriations",
    title: "Appropriations deadline",
    description: "",
    category: "budget",
    status: "active",
    policyAreas: [],
    createdAt: "2026-08-01T00:00:00Z",
    updatedAt: "2026-08-17T00:00:00Z",
    lastArticleDate: null,
    updateCount: 12,
    ...o,
  };
}

function bill(o: Partial<BillInFlight> = {}): BillInFlight {
  return {
    billId: "hr4901-119",
    title: "A bill to do a thing",
    chamber: "house",
    sponsorId: "R000001",
    sponsorName: "A Sponsor",
    sponsorParty: "D",
    sponsorState: "NY",
    sponsorThumbnailUrl: null,
    introducedDate: "2026-07-01",
    latestAction: "Reported out of committee",
    latestActionDate: "2026-08-11",
    stage: "IN_COMMITTEE",
    policyArea: "Health",
    congress: 119,
    billType: "hr",
    isLaw: false,
    mentionCount: 0,
    ...o,
  };
}

describe("formatEntryDate", () => {
  it("formats in UTC so an entry does not shift a day for west-coast readers", () => {
    expect(formatEntryDate("2026-08-18T23:30:00Z")).toBe("18 AUG");
  });

  it("zero-pads the day so the reference column stays aligned", () => {
    expect(formatEntryDate("2026-01-05")).toBe("05 JAN");
  });

  it("returns an empty string rather than 'NaN' for a bad date", () => {
    expect(formatEntryDate("")).toBe("");
  });
});

describe("buildRecordEntries", () => {
  it("gives every entry a docket reference tagged by source", () => {
    const [i, m, b] = [
      buildRecordEntries([issue()], [], []),
      buildRecordEntries([], [monitor()], []),
      buildRecordEntries([], [], [bill()]),
    ];
    expect(i[0].ref).toBe("ISSUE-7");
    expect(m[0].ref).toBe("MON-3");
    expect(b[0].ref).toBe("HR4901-119");
  });

  it("sorts the merged feeds newest first", () => {
    const entries = buildRecordEntries(
      [issue({ id: 1, date: "2026-08-10" })],
      [monitor({ id: 2, updatedAt: "2026-08-19T00:00:00Z" })],
      [bill({ billId: "s100-119", latestActionDate: "2026-08-15" })]
    );
    expect(entries.map((e) => e.ref)).toEqual(["MON-2", "S100-119", "ISSUE-1"]);
  });

  it("caps the list so the index stays a front page, not a feed", () => {
    const many = Array.from({ length: 20 }, (_, n) => issue({ id: n, date: "2026-08-18" }));
    expect(buildRecordEntries(many, [], [])).toHaveLength(6);
  });

  it("drops monitors the backend no longer considers active", () => {
    const entries = buildRecordEntries([], [monitor({ id: 9, status: "closed" })], []);
    expect(entries).toHaveLength(0);
  });

  it("prefers a monitor's last article date over its updated timestamp", () => {
    // updatedAt moves on any write; lastArticleDate is when the record
    // actually gained something, which is what this index claims to show.
    const [entry] = buildRecordEntries(
      [],
      [monitor({ lastArticleDate: "2026-08-02T00:00:00Z", updatedAt: "2026-08-17T00:00:00Z" })],
      []
    );
    expect(entry.date).toBe("2026-08-02T00:00:00Z");
  });

  it("skips entries with no title or an unparseable date instead of rendering blanks", () => {
    const entries = buildRecordEntries(
      [issue({ id: 1, title: "" }), issue({ id: 2, date: "nonsense" })],
      [],
      []
    );
    expect(entries).toHaveLength(0);
  });

  it("truncates a long detail on a word boundary", () => {
    const long = "word ".repeat(40).trim();
    const [entry] = buildRecordEntries([issue({ summary: long })], [], []);
    expect(entry.detail.endsWith("…")).toBe(true);
    expect(entry.detail).not.toMatch(/\s…$/);
    expect(entry.detail.length).toBeLessThanOrEqual(79);
  });

  it("falls back to policy areas when an issue has no summary", () => {
    const [entry] = buildRecordEntries(
      [issue({ summary: "", policyAreas: ["Health", "Budget"] })],
      [],
      []
    );
    expect(entry.detail).toBe("Health · Budget");
  });

  it("singularises a monitor with exactly one update", () => {
    const [one] = buildRecordEntries([], [monitor({ updateCount: 1 })], []);
    expect(one.detail).toBe("1 update tracked");
  });
});
