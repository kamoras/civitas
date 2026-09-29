import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import ExplorePage from "./page";

const api = vi.hoisted(() => ({
  searchExplore: vi.fn(),
  fetchExploreStats: vi.fn(),
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
});
