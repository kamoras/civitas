import { describe, expect, it } from "vitest";
import { issueCardTexts } from "./route";

const news = {
  publicId: "i0000abcd",
  title: "Agency sues a state over its ballots",
  summary: "The agency sued state election officials Thursday.",
  summarySource: "Outlet B",
  date: "2026-10-15",
  status: "confirmed",
  sourceNames: ["Outlet A", "Outlet B"],
  imageUrl: "/photo/issue/1",
  imageCredit: "A. Photographer/Wire",
};

describe("issue link-preview card", () => {
  it("carries the day, whose words the summary is, the photo credit, the sources and the link", () => {
    const t = issueCardTexts(news);
    expect(t.date).toBe("October 15, 2026");
    expect(t.summaryCredit).toBe("Quoted from Outlet B");
    expect(t.photoCredit).toBe("Photo: A. Photographer/Wire");
    expect(t.sources).toBe("Sources: Outlet A, Outlet B");
    expect(t.link).toBe("civitas-research.org/issue/i0000abcd");
    expect(t.section).toBe("ACTION CENTER");
  });

  it("never labels a news story as federal data", () => {
    expect(Object.values(issueCardTexts(news)).join(" ")).not.toMatch(/FEDERAL DATA/);
  });

  it("names a developing story's one record, and says it is developing", () => {
    const t = issueCardTexts({
      ...news,
      status: "developing",
      sourceNames: ["Federal Register"],
      summarySource: null,
      imageCredit: null,
    });
    expect(t.section).toBe("ACTION CENTER · DEVELOPING");
    expect(t.sources).toBe("Source: Federal Register");
    expect(t.summaryCredit).toBe("");
    expect(t.photoCredit).toBe("");
  });

  it("names three sources and counts the rest", () => {
    const t = issueCardTexts({ ...news, sourceNames: ["A", "B", "C", "D", "E"] });
    expect(t.sources).toBe("Sources: A, B, C and 2 more");
  });

  it("credits no one for a summary the card leaves out", () => {
    const t = issueCardTexts({ ...news, summary: news.title });
    expect(t.summary).toBe("");
    expect(t.summaryCredit).toBe("");
  });

  it("keeps the generic card for an unknown issue", () => {
    const t = issueCardTexts(null);
    expect(t.title).toBe("Civitas Research Action Center");
    expect(t.link).toBe("civitas-research.org");
    expect(t.date).toBe("");
  });
});
