import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  captureImageData,
  captureScale,
  displayUrl,
  footerFacts,
  issueShareMeta,
  sectionMeta,
  sectionUrl,
  shareFileName,
  shareMetaAttrs,
  type ShareSubject,
} from "./shareImage";
import type { ActionIssue } from "@/types/action";

describe("sectionUrl / displayUrl", () => {
  it("appends the section anchor, replacing any existing one", () => {
    expect(sectionUrl("https://civitas-research.org/politicians/x", "votes")).toBe(
      "https://civitas-research.org/politicians/x#votes"
    );
    expect(sectionUrl("https://civitas-research.org/politicians/x#old", "votes")).toBe(
      "https://civitas-research.org/politicians/x#votes"
    );
  });

  it("prints the link without its scheme", () => {
    expect(displayUrl("https://civitas-research.org/issue/i1234abcd")).toBe(
      "civitas-research.org/issue/i1234abcd"
    );
  });
});

describe("shareFileName", () => {
  it("names the file by page and section", () => {
    expect(
      shareFileName("https://civitas-research.org/politicians/tim-burchett", "funding-independence")
    ).toBe("civitas-tim-burchett-funding-independence.png");
  });

  it("ignores the query string and cleans up odd characters", () => {
    expect(shareFileName("https://civitas-research.org/action?issue=i1", "issue-i1")).toBe(
      "civitas-action-issue-i1.png"
    );
    expect(shareFileName("https://civitas-research.org/congress/bills/H.R.%201", "Votes")).toBe(
      "civitas-h-r-201-votes.png"
    );
  });
});

describe("captureScale", () => {
  it("captures at the screen's ratio, at least 2x and at most 3x", () => {
    expect(captureScale(400, 600, 1)).toBe(2);
    expect(captureScale(400, 600, 3)).toBe(3);
    expect(captureScale(400, 600, 4)).toBe(3);
  });

  // The bug: a long contest drawer on an iPhone (3x) exceeded Safari's
  // 16,777,216-pixel canvas limit, and the capture failed outright.
  it("lowers the ratio so a tall section still fits a canvas every browser allocates", () => {
    const scale = captureScale(390, 6000, 3);
    expect(scale).toBeLessThan(3);
    expect((390 + 40) * scale * (6000 + 260) * scale).toBeLessThanOrEqual(16_777_216);
    expect((6000 + 260) * scale).toBeLessThanOrEqual(16_000);
  });
});

describe("captureImageData", () => {
  const fetchSpy = vi.fn();
  beforeEach(() => {
    fetchSpy.mockReset();
    vi.stubGlobal("fetch", fetchSpy);
  });
  afterEach(() => vi.unstubAllGlobals());

  it("lets same-origin and inline images load normally", async () => {
    expect(await captureImageData(`${window.location.origin}/_next/static/a.png`)).toBe(false);
    expect(await captureImageData("/icon.svg")).toBe(false);
    expect(await captureImageData("data:image/png;base64,aaaa")).toBe(false);
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("loads a site photo like any same-origin image", async () => {
    expect(await captureImageData("/photo/bioguide/B001309")).toBe(false);
  });

  // A capture must never make the visitor's browser request a third-party
  // host (AGENTS.md §8) — a third-party image is left blank, unfetched.
  it("leaves a third-party image blank without requesting it", async () => {
    const data = await captureImageData("https://images.example-news.com/photo.jpg");
    expect(fetchSpy).not.toHaveBeenCalled();
    expect(data).toMatch(/^data:image\/png;base64,/);
  });
});

// A picture travels without its page: every capture states where its
// figures come from, when they are as of, and the scale a score is on.
describe("footer facts", () => {
  const subject: ShareSubject = {
    title: "Jordan Hale",
    url: "https://civitas-research.org/politicians/jordan-hale",
    source: "FEC filings; House Clerk roll calls",
    asOf: "2026-10-14",
    scaleMax: 100,
  };
  const today = new Date(2026, 9, 15);

  it("states the source, the as-of date, the scale and the capture date", () => {
    const section = document.createElement("section");
    expect(footerFacts(sectionMeta(section, subject), today)).toEqual({
      source: "Source: FEC filings; House Clerk roll calls",
      dates: "Data as of Oct 14, 2026 · scores out of 100 · captured Oct 15, 2026",
    });
  });

  it("lets a section name its own source and date, and drop the scale", () => {
    const section = document.createElement("section");
    for (const [k, v] of Object.entries(
      shareMetaAttrs({
        source: "Annual financial disclosure",
        asOf: "2025-12-31T00:00:00",
        scaleMax: null,
      })
    ))
      section.setAttribute(k, v);
    expect(footerFacts(sectionMeta(section, subject), today)).toEqual({
      source: "Source: Annual financial disclosure",
      dates: "Data as of Dec 31, 2025 · captured Oct 15, 2026",
    });
  });

  it("dates figures read live by the capture day", () => {
    const section = document.createElement("section");
    expect(
      footerFacts(sectionMeta(section, { ...subject, asOf: null, scaleMax: undefined }), today)
    ).toEqual({
      source: "Source: FEC filings; House Clerk roll calls",
      dates: "Data as of Oct 15, 2026",
    });
  });

  it("credits an issue to the outlets it quotes, or to the official record", () => {
    const issue = {
      sourceNames: ["AP", "NPR", "AP"],
      date: "2026-10-14",
      countAsOf: null,
    } as unknown as ActionIssue;
    expect(issueShareMeta(issue)).toEqual({ source: "AP; NPR, quoted", asOf: "2026-10-14" });
    expect(issueShareMeta({ ...issue, sourceNames: [] }).source).toBe(
      "the official record linked on the issue's page"
    );
  });
});
