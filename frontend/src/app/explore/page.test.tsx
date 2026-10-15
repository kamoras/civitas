import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import ExplorePage from "./page";

const api = vi.hoisted(() => ({
  searchExplore: vi.fn(),
  fetchExploreStats: vi.fn(),
  fetchCommitteeRecordTotal: vi.fn(),
}));
vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  ...api,
}));
let params = new URLSearchParams();
vi.mock("next/navigation", () => ({ useSearchParams: () => params }));
vi.mock("@/components/layout/Navbar", () => ({ default: () => <header /> }));
vi.mock("@/components/layout/Footer", () => ({ default: () => <footer /> }));
vi.mock("@/components/BackToTop", () => ({ default: () => null }));

const doc = (id: number, title: string) => ({
  id,
  title,
  date: "2026-08-15",
  docType: "Senate Floor Speech",
  source: "Congressional Record",
  politicianName: "Avery Holt",
  politicianId: "S000",
  chamber: "Senate",
  distance: null,
  snippet: "",
  matchedBy: [],
  url: "https://www.congress.gov/x",
});

function open(search: string) {
  params = new URLSearchParams(search);
  window.history.replaceState(null, "", `/explore${search ? `?${search}` : ""}`);
  api.fetchExploreStats.mockResolvedValue({ totalDocuments: 0, openForComment: 0 });
  if (!api.fetchCommitteeRecordTotal.getMockImplementation()) {
    api.fetchCommitteeRecordTotal.mockResolvedValue(0);
  }
  return render(<ExplorePage />);
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("ExplorePage", () => {
  it("lists a member's documents when opened from their profile's view-all link", async () => {
    // /explore?politician_id= with no query used to render an empty search
    // page — the profile's "view all N documents" link led nowhere.
    api.searchExplore.mockResolvedValue({
      query: "",
      results: [doc(1, "Remarks on roads")],
      count: 1,
    });
    open("politician_id=S000");

    expect(await screen.findByText("Remarks on roads")).toBeInTheDocument();
    expect(api.searchExplore).toHaveBeenCalledWith(
      "",
      expect.objectContaining({ politicianId: "S000", sort: "date" })
    );
    expect(screen.getByText(/Only documents from/)).toHaveTextContent("Avery Holt");
    expect(screen.getByRole("button", { name: "Newest" })).toHaveAttribute("aria-pressed", "true");
  });

  it("puts a new search in the address, so a refresh shows the same results", async () => {
    api.searchExplore.mockResolvedValue({ query: "b", results: [], count: 0 });
    open("q=energy");
    const box = screen.getByRole("searchbox", { name: /search government records/i });

    fireEvent.change(box, { target: { value: "appliances" } });
    fireEvent.submit(box.closest("form")!);

    expect(window.location.search).toBe("?q=appliances");
  });

  it("drops the member filter from both the results and the address", async () => {
    api.searchExplore.mockResolvedValue({ query: "roads", results: [doc(1, "Remarks")], count: 1 });
    open("q=roads&politician_id=S000");
    await screen.findByText("Remarks");

    fireEvent.click(screen.getByRole("button", { name: "SEARCH EVERYONE" }));

    expect(window.location.search).toBe("?q=roads");
    expect(screen.queryByText(/Only documents from/)).not.toBeInTheDocument();
    expect(api.searchExplore).toHaveBeenLastCalledWith(
      "roads",
      expect.objectContaining({ politicianId: undefined })
    );
  });

  it("lists every open comment period from ?comment=open, with no query", async () => {
    // The homepage's "Every open comment period" link used to open an empty
    // search page: the comment filter did nothing without a search term.
    api.searchExplore.mockResolvedValue({ query: "", results: [doc(1, "A rule")], count: 1 });
    open("comment=open");

    expect(await screen.findByText("A rule")).toBeInTheDocument();
    expect(api.searchExplore).toHaveBeenCalledWith(
      "",
      expect.objectContaining({ commentableOnly: true })
    );
    expect(screen.getByRole("heading", { level: 2 })).toHaveTextContent(
      /1 open comment period.*sorted by closing date/
    );
  });

  it("lists open comment periods when the filter is switched on with an empty box", async () => {
    api.searchExplore.mockResolvedValue({ query: "", results: [doc(1, "A rule")], count: 1 });
    open("");
    fireEvent.click(screen.getByRole("button", { name: /open for public comment/i }));

    expect(await screen.findByText("A rule")).toBeInTheDocument();
    expect(window.location.search).toBe("?comment=open");
  });

  it("offers a searched bill number's own page above the results", async () => {
    api.searchExplore.mockResolvedValue({ query: "H.R. 7008", results: [], count: 0 });
    open("q=H.R.%207008");

    const link = await screen.findByRole("link", { name: /Open the page for H\.R\. 7008/ });
    expect(link).toHaveAttribute("href", "/congress/bills/HR.7008");
  });

  it("offers the committee meetings and reports that match a search", async () => {
    api.searchExplore.mockResolvedValue({
      query: "veterans",
      results: [doc(1, "Remarks")],
      count: 1,
    });
    api.fetchCommitteeRecordTotal.mockResolvedValue(12);
    open("q=veterans");
    const link = await screen.findByRole("link", { name: /12 committee meetings and reports/ });
    expect(link).toHaveAttribute("href", "/congress/committees?q=veterans#committee-results");
    expect(api.fetchCommitteeRecordTotal).toHaveBeenCalledWith("veterans");
    api.fetchCommitteeRecordTotal.mockReset();
  });
});
