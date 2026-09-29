import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import type { ActionIssue } from "@/types/action";

vi.mock("next/navigation", () => ({
  notFound: vi.fn(() => {
    throw new Error("NOT_FOUND");
  }),
}));
vi.mock("@/components/layout/Navbar", () => ({ default: () => <header /> }));
vi.mock("@/components/layout/Footer", () => ({ default: () => <footer /> }));
vi.mock("@/components/BackToTop", () => ({ default: () => null }));
vi.mock("./IssueActions", () => ({ default: () => null }));

import IssuePage from "./page";

const COUNT_FACTS = [
  "Jane Doe (D): 101,234 votes, 50.4%",
  "John Roe (R): 99,876 votes, 49.6%",
  "412 of 800 precincts reporting (52%)",
  "The seat is held by a Republican going into this election",
];

function issue(over: Partial<ActionIssue> = {}): ActionIssue {
  return {
    id: 7,
    publicId: "abc123",
    date: "2026-11-03",
    firstSurfaced: "2026-11-03",
    rank: 999,
    title: "Democrat leads Utah's 1st Congressional District count in a seat Republicans hold",
    summary: "Utah Lieutenant Governor's count shows Jane Doe (D) ahead of John Roe (R).",
    facts: COUNT_FACTS,
    factSources: [],
    newFacts: [],
    actions: [],
    sourceUrls: [],
    sourceNames: [],
    policyAreas: [],
    relatedBills: [],
    relatedExploreDocs: [],
    relatedSenators: [],
    concernedCount: 0,
    notPriorityCount: 0,
    isTrending: false,
    status: "developing",
    sourceType: "election_results",
    ...over,
  } as ActionIssue;
}

async function renderIssue(data: ActionIssue) {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => data })
  );
  render(await IssuePage({ params: Promise.resolve({ id: data.publicId }) }));
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  // 9:41 PM Eastern on election night.
  vi.setSystemTime(new Date("2026-11-04T02:41:00Z"));
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

function factsSection() {
  return screen
    .getByRole("heading", { name: /from the count|media coverage/i })
    .closest("section")!;
}

describe("an issue's facts section", () => {
  it("is anchored and shared as #from-the-count for a count", async () => {
    await renderIssue(issue());
    const section = factsSection();
    expect(section.id).toBe("from-the-count");
    expect(section.getAttribute("data-share-section")).toBe("from-the-count");
  });

  it("keeps #media-coverage for a news issue, so existing links still land", async () => {
    await renderIssue(issue({ sourceType: null, status: "confirmed", facts: ["A quote."] }));
    const section = factsSection();
    expect(section.id).toBe("media-coverage");
    expect(section.getAttribute("data-share-section")).toBe("media-coverage");
  });

  it("says, inside the shared section, that the count is not final and when it was read (ET)", async () => {
    await renderIssue(issue());
    const section = factsSection();
    expect(section.textContent).toMatch(/NOT FINAL/);
    expect(section.textContent).toMatch(/the count as of Nov 3, 9:41 PM ET/);
  });

  it("calls an official count official, not 'not final'", async () => {
    await renderIssue(
      issue({
        title:
          "Democrat wins Utah's 1st Congressional District in the official count, taking a seat Republicans held",
      })
    );
    const section = factsSection();
    expect(section.textContent).toMatch(/OFFICIAL COUNT/);
    expect(section.textContent).not.toMatch(/NOT FINAL/);
  });

  it("adds no count line to a news issue, nor to a count issue confirmed by coverage", async () => {
    await renderIssue(
      issue({ sourceType: "election_results", status: "confirmed", facts: ["A quote."] })
    );
    const section = factsSection();
    expect(section.id).toBe("media-coverage");
    expect(section.textContent).not.toMatch(/NOT FINAL|OFFICIAL COUNT|count as of/);
  });
});
