import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import ExplorePage from "./page";

const api = vi.hoisted(() => ({
  searchExplore: vi.fn(),
  fetchExploreStats: vi.fn(),
  fetchCommitteeRecordTotal: vi.fn(),
  fetchExploreMatches: vi.fn(),
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

const NO_MATCHES = { members: { total: 0, items: [] }, bills: { total: 0, items: [] } };

function open(search: string) {
  params = new URLSearchParams(search);
  window.history.replaceState(null, "", `/explore${search ? `?${search}` : ""}`);
  api.fetchExploreStats.mockResolvedValue({ totalDocuments: 0, openForComment: 0 });
  if (!api.fetchCommitteeRecordTotal.getMockImplementation()) {
    api.fetchCommitteeRecordTotal.mockResolvedValue(0);
  }
  if (!api.fetchExploreMatches.getMockImplementation()) {
    api.fetchExploreMatches.mockResolvedValue(NO_MATCHES);
  }
  return render(<ExplorePage />);
}

// jsdom has neither; `phone` decides what a (max-width) query answers.
let phone = false;
const scrollIntoView = vi.fn();
beforeEach(() => {
  phone = false;
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    value: (query: string) => ({ matches: phone && query.includes("max-width") }),
  });
  Element.prototype.scrollIntoView = scrollIntoView;
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  Reflect.deleteProperty(window, "matchMedia");
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

  it("lists an agency's documents from an agency page's link, and filters by another", async () => {
    api.searchExplore.mockResolvedValue({
      query: "",
      results: [
        {
          ...doc(2, "Food labels"),
          chamber: "Regulatory",
          agencyName: "Food and Drug Administration",
          agencySlug: "food-and-drug-administration",
        },
      ],
      count: 1,
    });
    params = new URLSearchParams("agency=health-and-human-services-department");
    window.history.replaceState(null, "", "/explore?agency=health-and-human-services-department");
    api.fetchExploreStats.mockResolvedValue({
      totalDocuments: 2,
      openForComment: 0,
      agencies: [
        {
          slug: "health-and-human-services-department",
          name: "Health and Human Services Department",
          depth: 0,
          documents: 2,
        },
        {
          slug: "food-and-drug-administration",
          name: "Food and Drug Administration",
          depth: 1,
          documents: 1,
        },
      ],
    });
    render(<ExplorePage />);

    expect(await screen.findByText("Food labels")).toBeInTheDocument();
    expect(api.searchExplore).toHaveBeenCalledWith(
      "",
      expect.objectContaining({ agency: "health-and-human-services-department", sort: "date" })
    );
    expect(
      await screen.findByText(/document from Health and Human Services Department/)
    ).toBeInTheDocument();
    fireEvent.change(screen.getByRole("combobox", { name: /Agency/ }), {
      target: { value: "food-and-drug-administration" },
    });
    expect(api.searchExplore).toHaveBeenLastCalledWith(
      "",
      expect.objectContaining({ agency: "food-and-drug-administration" })
    );
    expect(window.location.search).toBe("?agency=food-and-drug-administration");
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

  it("offers a searched bill number's own page, in the Bills group", async () => {
    api.searchExplore.mockResolvedValue({ query: "H.R. 7008", results: [], count: 0 });
    api.fetchExploreMatches.mockResolvedValue({
      members: { total: 0, items: [] },
      bills: {
        total: 1,
        items: [
          {
            billId: "HR.7008",
            congress: 119,
            label: "H.R. 7008",
            title: "A bill to restrict stock trading",
            shortTitle: null,
            stage: "REFERRED",
          },
        ],
      },
    });
    open("q=H.R.%207008");

    const link = await screen.findByRole("link", {
      name: /H\.R\. 7008.*A bill to restrict stock trading/,
    });
    expect(link).toHaveAttribute("href", "/congress/bills/HR.7008?congress=119");
    api.fetchExploreMatches.mockReset();
  });

  it("offers the agencies a search names, in the Agencies group", async () => {
    api.searchExplore.mockResolvedValue({ query: "aviation", results: [], count: 0 });
    api.fetchExploreMatches.mockResolvedValue({
      members: { total: 0, items: [] },
      bills: { total: 0, items: [] },
      agencies: {
        total: 1,
        items: [
          {
            slug: "federal-aviation-administration",
            name: "Federal Aviation Administration",
            shortName: "FAA",
          },
        ],
      },
    });
    open("q=aviation");

    const link = await screen.findByRole("link", { name: /Federal Aviation Administration/ });
    expect(link).toHaveAttribute("href", "/agencies/federal-aviation-administration");
    expect(screen.getByRole("heading", { name: /Agencies/ })).toBeInTheDocument();
    api.fetchExploreMatches.mockReset();
  });

  it("still opens a bill number's page when the groups can't be read", async () => {
    api.searchExplore.mockResolvedValue({ query: "H.R. 7008", results: [], count: 0 });
    api.fetchExploreMatches.mockRejectedValue(new Error("502"));
    open("q=H.R.%207008");

    const link = await screen.findByRole("link", { name: /Open the page for H\.R\. 7008/ });
    expect(link).toHaveAttribute("href", "/congress/bills/HR.7008");
    api.fetchExploreMatches.mockReset();
  });

  it("names the members a search names, beside the documents, not among them", async () => {
    api.searchExplore.mockResolvedValue({
      query: "holt",
      results: [doc(1, "Remarks")],
      count: 1,
    });
    api.fetchExploreMatches.mockResolvedValue({
      members: {
        total: 1,
        items: [
          {
            id: "avery-holt",
            name: "Avery Holt",
            party: "D",
            state: "SW",
            chamber: "senate",
            district: null,
            thumbnailUrl: null,
          },
        ],
      },
      bills: { total: 0, items: [] },
    });
    open("q=holt");

    const group = (await screen.findByRole("heading", { name: /Members 1 match/ })).closest(
      "section"
    )!;
    expect(within(group).getByRole("link", { name: /Avery Holt/ })).toHaveAttribute(
      "href",
      "/politicians/avery-holt"
    );
    // The document results are untouched: one result, as searched.
    expect(screen.getByText(/1 result for/)).toBeInTheDocument();
    api.fetchExploreMatches.mockReset();
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

  it("offers a failed search again, without sending readers to the admin panel", async () => {
    api.searchExplore.mockRejectedValueOnce(new Error("Explore search failed: 502"));
    api.searchExplore.mockResolvedValueOnce({
      query: "roads",
      results: [doc(1, "Remarks")],
      count: 1,
    });
    open("q=roads");

    fireEvent.click(await screen.findByRole("button", { name: "TRY AGAIN" }));

    expect(await screen.findByText("Remarks")).toBeInTheDocument();
    expect(screen.queryByText(/admin panel/)).not.toBeInTheDocument();
  });

  it("brings the query box to the top on a phone when the reader searches", () => {
    // The first result sat a whole screen down at 375 px.
    api.searchExplore.mockResolvedValue({ query: "roads", results: [], count: 0 });
    phone = true;
    open("");
    const box = screen.getByRole("searchbox", { name: /search government records/i });
    fireEvent.change(box, { target: { value: "roads" } });
    fireEvent.submit(box.closest("form")!);
    expect(scrollIntoView).toHaveBeenCalledWith({ block: "start", behavior: "smooth" });
  });

  it("leaves the page where it is on a wider screen", () => {
    api.searchExplore.mockResolvedValue({ query: "roads", results: [], count: 0 });
    open("");
    const box = screen.getByRole("searchbox", { name: /search government records/i });
    fireEvent.change(box, { target: { value: "roads" } });
    fireEvent.submit(box.closest("form")!);
    expect(scrollIntoView).not.toHaveBeenCalled();
  });
});
