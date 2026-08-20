import { describe, expect, it } from "vitest";
import { buildRecordEntries, formatEntryDate } from "./RecordIndex";
import type { NationalMonitor } from "@/lib/api";
import type { ActionIssue } from "@/types/action";
import type { BillInFlight } from "@/types/bill";

function issue(o: Partial<ActionIssue> = {}): ActionIssue {
  return {
    id: 7,
    publicId: "i00000007",
    date: "2026-08-18",
    firstSurfaced: "2026-08-18",
    isTrending: false,
    rank: 1,
    title: "Prescription drug pricing",
    summary: "A summary of the issue.",
    facts: [],
    newFacts: [],
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

  it("treats an offset-less datetime as UTC — the shape monitors and bills send", () => {
    // The suite runs pinned to America/Los_Angeles (see vitest.config.ts)
    // precisely so this case can fail. 20:00 UTC on the 17th is 03:00 UTC on
    // the 18th if the string is mis-parsed as local, so a plain `new Date`
    // here reports the wrong DAY, not merely the wrong hour. Issue dates are
    // date-only and would parse as UTC either way, which is why the bug hid.
    expect(formatEntryDate("2026-08-17T20:00:00")).toBe("17 AUG");
  });
});

describe("buildRecordEntries", () => {
  it("gives every entry a docket reference tagged by source", () => {
    const [i, m, b] = [
      buildRecordEntries([issue()], [], []),
      buildRecordEntries([], [monitor()], []),
      buildRecordEntries([], [], [bill()]),
    ];
    expect(i[0].ref).toBe("ISSUE-I00000007");
    expect(m[0].ref).toBe("MON-3");
    expect(b[0].ref).toBe("HR4901-119");
  });

  it("uses the public id, not the raw autoincrement id — that's the whole reason it exists", () => {
    // #398 regression: the homepage docket reference was quoting the raw
    // int id (a running count of every issue ever), same bug as the
    // Action Center's ISSUE- label had before publicId existed.
    const [entry] = buildRecordEntries([issue({ id: 999, publicId: "iabcdef01" })], [], []);
    expect(entry.ref).toBe("ISSUE-IABCDEF01");
    expect(entry.href).toBe("/issue/iabcdef01"); // href stays lower-case — it's a real URL, not a label
  });

  it("sorts the merged feeds newest first", () => {
    const entries = buildRecordEntries(
      [issue({ id: 1, publicId: "i00000001", date: "2026-08-10" })],
      [monitor({ id: 2, updatedAt: "2026-08-19T00:00:00Z" })],
      [bill({ billId: "s100-119", latestActionDate: "2026-08-15" })]
    );
    expect(entries.map((e) => e.ref)).toEqual(["MON-2", "S100-119", "ISSUE-I00000001"]);
  });

  it("orders a date-only issue against an offset-less monitor timestamp correctly", () => {
    // The two feeds use different ISO shapes, and ECMA-262 parses them under
    // different rules — date-only as UTC, offset-less date-time as local. Sorting
    // them with a bare `new Date` silently interleaves the sources wrongly.
    const entries = buildRecordEntries(
      [issue({ id: 1, publicId: "i00000001", date: "2026-08-18" })],
      [monitor({ id: 2, lastArticleDate: "2026-08-17T20:00:00" })],
      []
    );
    expect(entries.map((e) => e.ref)).toEqual(["ISSUE-I00000001", "MON-2"]);
  });

  it("caps the list so the index stays a front page, not a feed", () => {
    const many = Array.from({ length: 20 }, (_, n) => issue({ id: n, date: "2026-08-18" }));
    expect(buildRecordEntries(many, [], [])).toHaveLength(6);
  });

  it("drops dormant (watching) monitors", () => {
    // Not "closed": /action/monitors already filters to ACTIVE + WATCHING, so
    // a closed monitor can never reach this code and asserting on one proves
    // nothing. WATCHING is what the filter actually excludes — a monitor gone
    // dormant past the article cutoff, whose updatedAt the pipeline touches
    // when it flips the status.
    const entries = buildRecordEntries([], [monitor({ id: 9, status: "watching" })], []);
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
