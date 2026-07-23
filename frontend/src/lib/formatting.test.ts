import { describe, expect, it } from "vitest";
import { formatCurrency, formatUtcDate, localDateStr, safeHref } from "./formatting";

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
    expect(formatUtcDate("2026-07-04", { year: "numeric", month: "long", day: "numeric" }, "en-US"))
      .toBe("July 4, 2026");
  });

  it("returns an empty string for an empty input", () => {
    expect(formatUtcDate("")).toBe("");
  });

  it("preserves the calendar date regardless of local timezone", () => {
    // Parsed as local noon specifically so a UTC-negative timezone can't
    // roll the date back to the previous day.
    const result = formatUtcDate("2026-01-01", { year: "numeric", month: "numeric", day: "numeric" }, "en-US");
    expect(result).toContain("2026");
    expect(result).toMatch(/1\/1\/2026|1\/1\/26/);
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
