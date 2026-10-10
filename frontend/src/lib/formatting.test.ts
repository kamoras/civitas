import { describe, expect, it } from "vitest";
import {
  asLabel,
  cashOnHandDisplay,
  commentDaysLeft,
  competitionRanks,
  displayScore,
  commentPeriodToday,
  describeDaysLeft,
  isCommentPeriodOpen,
  formatCurrency,
  formatUtcDate,
  formatWeekRange,
  issueDateLabel,
  isNewFact,
  issueRef,
  localDateStr,
  policyAreaLabel,
  restatesTitle,
  safeHref,
} from "./formatting";

describe("formatCurrency", () => {
  it.each([
    ["billions", 2_500_000_000, "$2.5B"],
    ["millions", 1_200_000, "$1.2M"],
    ["thousands", 45_000, "$45K"],
    ["sub-thousand amounts with locale grouping", 999, "$999"],
    ["zero", 0, "$0"],
  ])("formats %s", (_, amount, expected) => {
    expect(formatCurrency(amount)).toBe(expected);
  });

  it("puts the sign outside the dollar sign for negative amounts", () => {
    // The bug this guards against: operating on the raw (negative) value
    // skipped every magnitude threshold and fell through to the plain
    // toLocaleString branch, rendering "$-1,000,000" instead of "-$1.0M".
    expect(formatCurrency(-1_000_000)).toBe("-$1.0M");
    expect(formatCurrency(-500)).toBe("-$500");
  });

  it("rounds sub-thousand cents rather than showing them raw", () => {
    // Real FEC cash-on-hand figures carry cents; showing "$383.2" next
    // to "$200" in the same list read as inconsistent/buggy, not as
    // real precision (2026-08 review of live production data).
    expect(formatCurrency(383.2)).toBe("$383");
    expect(formatCurrency(944.54)).toBe("$945");
    expect(formatCurrency(-781.22)).toBe("-$781");
  });

  it("never prints a nonzero amount under a dollar as $0", () => {
    expect(formatCurrency(-0.04)).toBe("-$0.04");
    expect(formatCurrency(0.4)).toBe("$0.40");
    expect(formatCurrency(0.001)).toBe("$0.01");
  });
});

describe("cashOnHandDisplay", () => {
  it("shows a negative figure as the FEC reports it, not as debt", () => {
    expect(cashOnHandDisplay(-0.04)).toEqual({ label: "Cash on hand", amount: "-$0.04" });
    expect(cashOnHandDisplay(-3500)).toEqual({ label: "Cash on hand", amount: "-$4K" });
    expect(cashOnHandDisplay(null)).toBeNull();
  });
});

describe("localDateStr", () => {
  it("formats a given date as YYYY-MM-DD in local time", () => {
    // Month is 0-indexed; single-digit month and day are zero-padded.
    expect(localDateStr(new Date(2026, 6, 4))).toBe("2026-07-04");
  });
});

describe("formatUtcDate", () => {
  it("formats a date string using the given locale/options, keeping its calendar date", () => {
    // Parsed as local noon specifically so a UTC-negative timezone (the suite
    // runs in America/Los_Angeles) can't roll the date back to the previous day.
    expect(
      formatUtcDate("2026-07-04", { year: "numeric", month: "long", day: "numeric" }, "en-US")
    ).toBe("July 4, 2026");
  });

  it("returns an empty string for an empty input", () => {
    expect(formatUtcDate("")).toBe("");
  });
});

describe("issueDateLabel", () => {
  it("shows a single date when the story hasn't been re-matched since it surfaced", () => {
    expect(issueDateLabel({ date: "2026-08-19", firstSurfaced: "2026-08-19" })).toBe("2026-08-19");
  });

  it("shows both dates when a still-trending story's date has drifted from its origin", () => {
    expect(issueDateLabel({ date: "2026-08-20", firstSurfaced: "2026-08-15" })).toBe(
      "2026-08-15 · updated 2026-08-20"
    );
  });

  it("falls back to date alone rather than rendering the literal word 'undefined'", () => {
    // A real case, not a hypothetical: nginx's proxy_cache for this endpoint
    // can serve a response cached from before a deploy that added
    // firstSurfaced, for up to its own TTL regardless of how fresh the
    // backend already is (confirmed live, 2026-08-20).
    expect(issueDateLabel({ date: "2026-08-20", firstSurfaced: undefined })).toBe("2026-08-20");
    expect(issueDateLabel({ date: "2026-08-20", firstSurfaced: "" })).toBe("2026-08-20");
  });
});

describe("issueRef", () => {
  it("upper-cases the public id to match ISSUE's own capitalization", () => {
    expect(issueRef("i7a2c9f01")).toBe("ISSUE-I7A2C9F01");
  });

  it("never throws on a missing public id — a crash, not just a display glitch", () => {
    // The same stale-cache window issueDateLabel's fallback guards against
    // would otherwise call .toUpperCase() on undefined here.
    expect(issueRef(undefined)).toBe("ISSUE-");
  });
});

describe("isNewFact", () => {
  it("is true when the fact is in the new-facts list", () => {
    expect(isNewFact(["a new fact"], "a new fact")).toBe(true);
  });

  it("is false when the fact isn't in the list", () => {
    expect(isNewFact(["something else"], "a fact")).toBe(false);
  });

  it("never throws when newFacts is missing — this exact gap crashed the whole Action Center live", () => {
    // 2026-08-20: issue.newFacts.includes(fact) with no guard, called from a
    // response cached (browser or nginx) from before the field existed.
    expect(isNewFact(undefined, "a fact")).toBe(false);
  });
});

describe("safeHref", () => {
  it("allows http/https/mailto URLs", () => {
    expect(safeHref("https://example.com")).toBe("https://example.com");
    expect(safeHref("http://example.com")).toBe("http://example.com");
    expect(safeHref("mailto:a@example.com")).toBe("mailto:a@example.com");
  });

  it.each([
    // Rejected before parsing, so it can't reach an attacker's host.
    ["protocol-relative", "//evil.com"],
    ["javascript:", "javascript:alert(1)"],
    ["data:", "data:text/html,<script>alert(1)</script>"],
    ["unparseable", "http://[invalid"],
  ])("rejects %s URLs", (_, url) => {
    expect(safeHref(url)).toBeUndefined();
  });

  it("returns undefined for null/undefined/empty input", () => {
    expect(safeHref(null)).toBeUndefined();
    expect(safeHref(undefined)).toBeUndefined();
    expect(safeHref("")).toBeUndefined();
  });
});

describe("formatWeekRange", () => {
  it("names the month once for a week inside a single month", () => {
    // Exact, so ICU's best-fit rendering of a { day, year } pair can't leak:
    // { day: "numeric", year: "numeric" } is not a CLDR skeleton; ICU renders
    // it "2026 (day: 19)", which put "Jul 13–2026 (day: 19)" in the week header.
    expect(formatWeekRange("2026-07-13", "2026-07-19")).toBe("Jul 13–19, 2026");
  });

  it("names both months when the week crosses a month boundary", () => {
    expect(formatWeekRange("2026-06-29", "2026-07-05")).toBe("Jun 29–Jul 5, 2026");
  });

  it("falls back to the raw range for unparseable dates", () => {
    expect(formatWeekRange("", "")).toBe("–");
  });
});

describe("describeDaysLeft", () => {
  // Comment deadlines arrive as bare dates from regulations.gov; the reader's
  // timezone must not shift which day the countdown lands on.
  const asOf = Date.UTC(2026, 7, 18, 15, 0, 0);

  it.each([
    ["counts whole days to a future deadline", "2026-08-25", "7 days left"],
    ["says 'closes today' on the deadline itself", "2026-08-18", "closes today"],
    ["says 'closes today' for a deadline already past", "2026-08-01", "closes today"],
    ["uses the singular for the last full day", "2026-08-19", "1 day left"],
    ["returns '' for an unparseable date rather than 'NaN days left'", "not a date", ""],
  ])("%s", (_, closeDate, expected) => {
    expect(describeDaysLeft(closeDate, asOf)).toBe(expected);
  });

  it("treats an offset-less timestamp as UTC, not viewer-local", () => {
    // The suite runs in America/Los_Angeles (see vitest.config.mts). Parsed as
    // local time this would be 7-8h later and could round to a different day.
    expect(describeDaysLeft("2026-08-21T00:00:00", asOf)).toBe(
      describeDaysLeft("2026-08-21T00:00:00Z", asOf)
    );
  });
});

describe("comment deadlines are Eastern calendar dates", () => {
  // 2026-08-18 21:30 EDT is 2026-08-19 01:30 UTC: the UTC date has already
  // rolled over, but regulations.gov accepts comments until 11:59 PM ET.
  const lastEvening = Date.UTC(2026, 7, 19, 1, 30, 0);

  it("reads today's date in Eastern time, not UTC", () => {
    expect(commentPeriodToday(lastEvening)).toBe("2026-08-18");
  });

  it("keeps a period open through its final Eastern evening", () => {
    expect(isCommentPeriodOpen("2026-08-18", lastEvening)).toBe(true);
    expect(commentDaysLeft("2026-08-18", lastEvening)).toBe(0);
    expect(describeDaysLeft("2026-08-18", lastEvening)).toBe("closes today");
  });

  it("closes at Eastern midnight, whatever the reader's zone", () => {
    // 00:30 EDT on the 19th — 21:30 the previous evening in Los Angeles.
    const justAfter = Date.UTC(2026, 7, 19, 4, 30, 0);
    expect(isCommentPeriodOpen("2026-08-18", justAfter)).toBe(false);
  });

  it("counts calendar days, not 24-hour spans", () => {
    expect(commentDaysLeft("2026-08-19", lastEvening)).toBe(1);
    expect(commentDaysLeft("2026-08-25", lastEvening)).toBe(7);
  });

  it("treats a missing deadline as closed", () => {
    expect(isCommentPeriodOpen(null, lastEvening)).toBe(false);
    expect(isCommentPeriodOpen("", lastEvening)).toBe(false);
  });
});

describe("displayScore and competitionRanks", () => {
  it("shows overall scores as whole numbers", () => {
    expect(displayScore(56.8)).toBe(57);
    expect(displayScore(61.82)).toBe(62);
  });

  it("gives tied members the same rank and skips past the tie", () => {
    const rows = [{ s: 70 }, { s: 53 }, { s: 53 }, { s: 40 }];
    expect(competitionRanks(rows, (r) => r.s)).toEqual([1, 2, 2, 4]);
  });

  it("ranks ties on the displayed value", () => {
    const rows = [53.25, 53.49, 52.84].map((s) => ({ s }));
    expect(competitionRanks(rows, (r) => displayScore(r.s))).toEqual([1, 1, 1]);
  });

  it("continues numbering across pages", () => {
    expect(competitionRanks([{ s: 5 }, { s: 4 }], (r) => r.s, 50)).toEqual([51, 52]);
  });
});

describe("asLabel", () => {
  it("raises the first letter of a phrase shown on its own", () => {
    expect(asLabel("progressive Democrat leader")).toBe("Progressive Democrat leader");
    expect(asLabel("centrist Independent")).toBe("Centrist Independent");
    expect(asLabel("")).toBe("");
  });
});

describe("policyAreaLabel", () => {
  it("prints a code without its underscores", () => {
    expect(policyAreaLabel("FOREIGN_POLICY")).toBe("FOREIGN POLICY");
    expect(policyAreaLabel("TECH")).toBe("TECH");
  });
});

describe("restatesTitle", () => {
  it("catches a lede that is the headline with a period, in any case", () => {
    expect(restatesTitle("Senate passes the bill", "Senate passes the bill.")).toBe(true);
    expect(restatesTitle("Agency’s rule takes effect", "agency's rule takes effect")).toBe(true);
  });

  it("keeps a summary that says more", () => {
    expect(restatesTitle("Senate passes the bill", "Senate passes the bill, 52 to 48.")).toBe(
      false
    );
  });

  it("is false for no summary", () => {
    expect(restatesTitle("Senate passes the bill", "")).toBe(false);
    expect(restatesTitle("Senate passes the bill", null)).toBe(false);
  });

  it("compares letters in any script, so accents can't fake or hide a repeat", () => {
    expect(restatesTitle("Luján wins the seat", "Luján wins the seat.")).toBe(true);
    expect(restatesTitle("Luján wins the seat", "Lujan wins the seat")).toBe(false);
  });

  it("matches a summary that only repeats the headline", () => {
    expect(restatesTitle("Senate passes the bill", "Senate passes the bill.")).toBe(true);
    expect(restatesTitle("Senate passes the bill, AP says", "Senate passes the bill")).toBe(true);
  });
  it("keeps a summary that says something more", () => {
    expect(
      restatesTitle("Senate passes the bill", "The vote was 52-48 after a week of debate.")
    ).toBe(false);
    expect(restatesTitle("Senate passes the bill", "")).toBe(false);
    expect(restatesTitle("Senate passes the bill", undefined)).toBe(false);
  });
});
