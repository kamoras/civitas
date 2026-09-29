import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import ExploreDetailPage from "./page";

const api = vi.hoisted(() => ({
  fetchExploreDocument: vi.fn(),
  streamExploreDocumentSummary: vi.fn(),
  fetchDocumentComments: vi.fn(),
}));
vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  ...api,
}));
vi.mock("next/navigation", () => ({
  useParams: () => ({ id: "7" }),
  useSearchParams: () => new URLSearchParams(),
}));
vi.mock("@/components/layout/Navbar", () => ({ default: () => <header /> }));
vi.mock("@/components/layout/Footer", () => ({ default: () => <footer /> }));

function withDocument() {
  api.fetchExploreDocument.mockResolvedValue({
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
  });
  api.fetchDocumentComments.mockResolvedValue({ comments: [], totalElements: 0 });
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
    render(<ExploreDetailPage />);
    expect(await screen.findByText("Analysis unavailable. Try again later.")).toBeInTheDocument();
  });

  it("says another reader's analysis is being written on a 429", async () => {
    withDocument();
    api.streamExploreDocumentSummary.mockRejectedValue(new Error("Summary failed: 429"));
    render(<ExploreDetailPage />);
    expect(await screen.findByText(/is being written/)).toBeInTheDocument();
  });
});
