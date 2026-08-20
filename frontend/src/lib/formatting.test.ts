import { describe, expect, it } from "vitest";
import {
  describeDaysLeft,
  formatCurrency,
  formatUtcDate,
  formatWeekRange,
  issueDateLabel,
  localDateStr,
  safeHref,
} from "./formatting";

describe("formatCurrency", () => {
  it("formats billions", () => {
    expect(formatCurrency(2_500_000_000)).toBe("$2.5B");
  });

  it("formats millions", () => {
    expect(formatCurrency(1_200_000)).toBe("$1.2M");
  });

  it("formats thousands", () => {
    expect(formatCurrency(45_000)).toBe("$45K");
  });

  it("formats sub-thousand amounts with locale grouping", () => {
    expect(formatCurrency(999)).toBe("$999");
  });

  it("puts the sign outside the dollar sign for negative amounts", () => {
    // The bug this guards against: operating on the raw (negative) value
    // skipped every magnitude threshold and fell through to the plain
    // toLocaleString branch, rendering "$-1,000,000" instead of "-$1.0M".
    expect(formatCurrency(-1_000_000)).toBe("-$1.0M");
    expect(formatCurrency(-500)).toBe("-$500");
  });

  it("formats zero", () => {
    expect(formatCurrency(0)).toBe("$0");
  });

  it("rounds sub-thousand cents rather than showing them raw", () => {
    // Real FEC cash-on-hand figures carry cents; showing "$383.2" next
    // to "$200" in the same list read as inconsistent/buggy, not as
    // real precision (2026-08 review of live production data).
    expect(formatCurrency(383.2)).toBe("$383");
    expect(formatCurrency(944.54)).toBe("$945");
    expect(formatCurrency(-781.22)).toBe("-$781");
  });
});

describe("localDateStr", () => {
  it("formats a given date as YYYY-MM-DD in local time", () => {
    expect(localDateStr(new Date(2026, 6, 4))).toBe("2026-07-04"); // month is 0-indexed
  });

  it("zero-pads single-digit month and day", () => {
    expect(localDateStr(new Date(2026, 0, 5))).toBe("2026-01-05");
  });
});

describe("formatUtcDate", () => {
  it("formats a date string using the given locale/options", () => {
    expect(
      formatUtcDate("2026-07-04", { year: "numeric", month: "long", day: "numeric" }, "en-US")
    ).toBe("July 4, 2026");
  });

  it("returns an empty string for an empty input", () => {
    expect(formatUtcDate("")).toBe("");
  });

  it("preserves the calendar date regardless of local timezone", () => {
    // Parsed as local noon specifically so a UTC-negative timezone can't
    // roll the date back to the previous day.
    const result = formatUtcDate(
      "2026-01-01",
      { year: "numeric", month: "numeric", day: "numeric" },
      "en-US"
    );
    expect(result).toContain("2026");
    expect(result).toMatch(/1\/1\/2026|1\/1\/26/);
  });
});

describe("issueDateLabel", () => {
  it("shows a single date when the story hasn't been re-matched since it surfaced", () => {
    expect(issueDateLabel({ date: "2026-08-19", firstSurfaced: "2026-08-19" })).toBe(
      "2026-08-19"
    );
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

describe("safeHref", () => {
  it("allows http/https/mailto URLs", () => {
    expect(safeHref("https://example.com")).toBe("https://example.com");
    expect(safeHref("http://example.com")).toBe("http://example.com");
    expect(safeHref("mailto:a@example.com")).toBe("mailto:a@example.com");
  });

  it("rejects protocol-relative URLs before they can reach an attacker's host", () => {
    expect(safeHref("//evil.com")).toBeUndefined();
  });

  it("rejects javascript: URLs", () => {
    expect(safeHref("javascript:alert(1)")).toBeUndefined();
  });

  it("rejects data: URLs", () => {
    expect(safeHref("data:text/html,<script>alert(1)</script>")).toBeUndefined();
  });

  it("returns undefined for null/undefined/empty input", () => {
    expect(safeHref(null)).toBeUndefined();
    expect(safeHref(undefined)).toBeUndefined();
    expect(safeHref("")).toBeUndefined();
  });

  it("returns undefined for URLs the URL constructor can't parse at all", () => {
    expect(safeHref("http://[invalid")).toBeUndefined();
  });
});

describe("formatWeekRange", () => {
  it("names the month once for a week inside a single month", () => {
    expect(formatWeekRange("2026-07-13", "2026-07-19")).toBe("Jul 13–19, 2026");
  });

  it("names both months when the week crosses a month boundary", () => {
    expect(formatWeekRange("2026-06-29", "2026-07-05")).toBe("Jun 29–Jul 5, 2026");
  });

  it("does not leak ICU's best-fit rendering of a { day, year } pair", () => {
    // { day: "numeric", year: "numeric" } is not a CLDR skeleton; ICU renders
    // it "2026 (day: 19)", which put "Jul 13–2026 (day: 19)" in the week header.
    expect(formatWeekRange("2026-07-13", "2026-07-19")).not.toContain("(day:");
  });

  it("falls back to the raw range for unparseable dates", () => {
    expect(formatWeekRange("", "")).toBe("–");
  });
});

describe("describeDaysLeft", () => {
  // Comment deadlines arrive as bare dates from regulations.gov; the reader's
  // timezone must not shift which day the countdown lands on.
  const asOf = Date.UTC(2026, 7, 18, 15, 0, 0);

  it("counts whole days to a future deadline", () => {
    expect(describeDaysLeft("2026-08-25", asOf)).toBe("7 days left");
  });

  it("says 'closes today' on the deadline itself", () => {
    expect(describeDaysLeft("2026-08-18", asOf)).toBe("closes today");
  });

  it("says 'closes today' for a deadline already past", () => {
    expect(describeDaysLeft("2026-08-01", asOf)).toBe("closes today");
  });

  it("uses the singular for the last full day", () => {
    expect(describeDaysLeft("2026-08-19", asOf)).toBe("1 day left");
  });

  it("treats an offset-less timestamp as UTC, not viewer-local", () => {
    // The suite runs in America/Los_Angeles (see vitest.config.ts). Parsed as
    // local time this would be 7-8h later and could round to a different day.
    expect(describeDaysLeft("2026-08-21T00:00:00", asOf)).toBe(
      describeDaysLeft("2026-08-21T00:00:00Z", asOf)
    );
  });

  it("returns empty string for an unparseable date rather than 'NaN days left'", () => {
    expect(describeDaysLeft("not a date", asOf)).toBe("");
  });
});
