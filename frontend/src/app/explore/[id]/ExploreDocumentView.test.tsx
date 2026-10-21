import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import ExploreDocumentView from "./ExploreDocumentView";
import type { ExploreDocumentDetail } from "@/lib/api";

const api = vi.hoisted(() => ({
  streamExploreDocumentSummary: vi.fn(),
  fetchDocumentComments: vi.fn(),
}));
vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  ...api,
}));
vi.mock("@/components/layout/Navbar", () => ({ default: () => <header /> }));
vi.mock("@/components/layout/Footer", () => ({ default: () => <footer /> }));

const DOC: ExploreDocumentDetail = {
  id: 7,
  title: "An order",
  date: "2026-01-02",
  docType: "Executive Order",
  source: "Federal Register",
  politicianName: "",
  politicianId: "",
  chamber: "Executive",
  agencyName: "",
  url: "https://www.federalregister.gov/x",
  summary: "",
  body: "Text of the order.",
  commentUrl: "",
  commentsCloseOn: "",
};

function withDocument() {
  api.fetchDocumentComments.mockResolvedValue({ comments: [], totalElements: 0 });
}

function renderPage(doc: ExploreDocumentDetail = DOC, query = "") {
  return render(<ExploreDocumentView doc={doc} query={query} />);
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("Explore document analysis", () => {
  it("says the analysis is unavailable when generation produced nothing", async () => {
    // It used to leave the Analysis panel blank.
    withDocument();
    api.streamExploreDocumentSummary.mockResolvedValue({ summary: "", keyPoints: [], impact: "" });
    renderPage();
    expect(await screen.findByText("Analysis unavailable. Try again later.")).toBeInTheDocument();
  });

  it("says the analysis is unavailable when the request fails", async () => {
    // A wait (another reader's generation, a busy model) is retried inside
    // streamExploreDocumentSummary; what reaches the page is a failure.
    withDocument();
    api.streamExploreDocumentSummary.mockRejectedValue(new Error("Summary failed: 500"));
    renderPage();
    expect(await screen.findByText("Analysis unavailable. Try again later.")).toBeInTheDocument();
  });

  it("says it is queued while waiting to be let in", async () => {
    // A pulse alone for minutes behind other readers' analyses looked stuck.
    withDocument();
    api.streamExploreDocumentSummary.mockImplementation(
      (
        _id: number,
        _onDelta: unknown,
        _signal: unknown,
        _wait: unknown,
        onWaiting: (w: boolean) => void
      ) => {
        onWaiting(true);
        return new Promise(() => {});
      }
    );
    renderPage();
    expect(await screen.findByText(/this one is queued/)).toBeInTheDocument();
  });
});

describe("Explore document page", () => {
  it("offers the results back only to a reader who came from them", () => {
    withDocument();
    api.streamExploreDocumentSummary.mockReturnValue(new Promise(() => {}));
    renderPage();
    expect(screen.getByRole("link", { name: "← Search documents" })).toHaveAttribute(
      "href",
      "/search?tab=documents"
    );
    cleanup();
    renderPage(DOC, "tariffs");
    expect(screen.getByRole("link", { name: "← Back to results" })).toHaveAttribute(
      "href",
      "/search?tab=documents&q=tariffs"
    );
  });

  it("says what Civitas is, for a reader who arrived cold", () => {
    withDocument();
    api.streamExploreDocumentSummary.mockReturnValue(new Promise(() => {}));
    renderPage();
    expect(screen.getByText(/is a public record of the people who hold power/)).toBeInTheDocument();
  });

  it("gives the document's own Federal Register citation to cite", async () => {
    withDocument();
    api.streamExploreDocumentSummary.mockReturnValue(new Promise(() => {}));
    renderPage({ ...DOC, frCitation: "91 FR 48248", frDocNumber: "2026-15603" });
    expect(screen.getByRole("heading", { name: "Cite this document" })).toBeInTheDocument();
    expect(screen.getByText("91 FR 48248")).toBeInTheDocument();
    expect(screen.getByText("FR Doc. 2026-15603")).toBeInTheDocument();
    // Cited to who issued it, in the Federal Register, never to Civitas as
    // a dataset; Civitas is only where it was accessed.
    const cite = await screen.findByText(/^Executive Office of the President\. .*An order\./);
    expect(cite.textContent).toContain("Federal Register, 91, 48248 (FR Doc. 2026-15603).");
    expect(cite.textContent).not.toContain("[Data set]");
    expect(cite.textContent).toMatch(/accessed via Civitas Research/);
  });
});

describe("A floor speech's bill", () => {
  it("links the bill its Record heading names", async () => {
    withDocument();
    api.streamExploreDocumentSummary.mockResolvedValue({ summary: "", keyPoints: [], impact: "" });
    renderPage({
      ...DOC,
      title: "S. 2",
      docType: "Senate Floor Speech",
      chamber: "Senate",
      bills: [{ billId: "S.2", label: "S. 2", congress: 119 }],
    });
    const link = await screen.findByRole("link", { name: "S. 2" });
    expect(link.getAttribute("href")).toMatch(/^\/congress\/bills\/S\.2/);
    expect(screen.getByText(/^Bill:/)).toBeInTheDocument();
  });
});
