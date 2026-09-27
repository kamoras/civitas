import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import Holdings from "./Holdings";
import type { HoldingCategory, Holdings as HoldingsData } from "@/types/senator";

const fetchSenatorHoldings = vi.fn();
vi.mock("@/lib/api", () => ({
  fetchSenatorHoldings: (...args: unknown[]) => fetchSenatorHoldings(...args),
  fetchRepHoldings: vi.fn(),
}));

function category(overrides: Partial<HoldingCategory>): HoldingCategory {
  return {
    category: "STOCKS",
    label: "Stocks",
    color: "#3987e5",
    count: 1,
    unvaluedCount: 0,
    zeroValueCount: 0,
    valueLow: 1001,
    valueHigh: 15000,
    openEnded: false,
    weight: 8000.5,
    share: 1,
    ...overrides,
  };
}

function holdings(overrides: Partial<HoldingsData> = {}): HoldingsData {
  return {
    available: true,
    reportYear: 2025,
    reportLabel: "2025 annual report",
    asOfDate: "2025-12-31",
    filedDate: "2026-05-15",
    sourceUrl: "https://example.com/report",
    parsed: true,
    unreadableReason: null,
    laterFilingLabel: null,
    laterFilingUrl: null,
    holdingsCount: 2,
    unvaluedCount: 0,
    totalLow: 16002,
    totalHigh: 65000,
    totalOpenEnded: false,
    categories: [
      category({ category: "FUNDS", label: "Mutual funds & ETFs", color: "#d95926", valueLow: 15001, valueHigh: 50000, weight: 32500.5, share: 0.8 }),
      category({ share: 0.2 }),
    ],
    categoryFilter: null,
    holdings: [
      {
        assetName: "Index Fund", account: null, ticker: null, assetType: "MF", category: "FUNDS",
        categoryLabel: "Mutual funds & ETFs", owner: "self", valueText: "$15,001 - $50,000",
        valueLow: 15001, valueHigh: 50000, valueOpenEnded: false,
      },
    ],
    total: 2,
    page: 1,
    perPage: 15,
    totalPages: 1,
    ...overrides,
  };
}

beforeEach(() => fetchSenatorHoldings.mockReset());

describe("Holdings", () => {
  it("renders nothing when no report has been ingested", async () => {
    fetchSenatorHoldings.mockResolvedValue(holdings({ available: false }));
    const { container } = render(<Holdings memberId="S1" />);
    await waitFor(() => expect(fetchSenatorHoldings).toHaveBeenCalled());
    await waitFor(() => expect(container).toBeEmptyDOMElement());
  });

  it("selecting a slice filters and opens the list; the selection is what the server returned", async () => {
    fetchSenatorHoldings.mockResolvedValueOnce(holdings());
    render(<Holdings memberId="S1" />);
    const stocks = await screen.findByRole("button", { name: /^Stocks/ });

    fetchSenatorHoldings.mockResolvedValueOnce(holdings({ categoryFilter: "STOCKS", total: 1 }));
    await userEvent.click(stocks);

    expect(fetchSenatorHoldings).toHaveBeenLastCalledWith("S1", { page: 1, perPage: 15, category: "STOCKS" });
    await waitFor(() => expect(screen.getByRole("button", { name: /^Stocks/ })).toHaveAttribute("aria-pressed", "true"));
    expect(screen.getByText(/Stocks: 1 holding, largest first/)).toBeInTheDocument();
  });

  it("a failed filter keeps the previous selection and shows the error with the list collapsed", async () => {
    fetchSenatorHoldings.mockResolvedValueOnce(holdings());
    render(<Holdings memberId="S1" />);
    const stocks = await screen.findByRole("button", { name: /^Stocks/ });
    // Collapse the list the click would open, so the error must show outside it.
    fetchSenatorHoldings.mockRejectedValueOnce(new Error("Failed to load holdings"));
    await userEvent.click(stocks);
    await userEvent.click(screen.getByRole("button", { name: /INVESTMENTS & ASSETS/ }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Failed to load holdings");
    expect(screen.getByRole("button", { name: /^Stocks/ })).toHaveAttribute("aria-pressed", "false");
  });

  it("an open-ended sum shows only its floor, never a placeholder ceiling", async () => {
    fetchSenatorHoldings.mockResolvedValue(holdings({
      categories: [category({ valueLow: 50_000_000, valueHigh: 50_000_000, openEnded: true, weight: 5e7 })],
      totalLow: 50_000_000, totalHigh: 50_000_000, totalOpenEnded: true,
    }));
    render(<Holdings memberId="S1" />);
    expect(await screen.findByText(/Disclosed value \$50\.0M\+ across/)).toBeInTheDocument();
    expect(screen.queryByText(/\$50\.0M – \$50\.0M/)).not.toBeInTheDocument();
  });

  it("names 'none at year end' and 'no value stated' apart, and a category without a slice is still selectable", async () => {
    fetchSenatorHoldings.mockResolvedValue(holdings({
      categories: [
        category({}),
        category({ category: "OTHER", label: "Other", color: "#8a857d", count: 2, unvaluedCount: 1, zeroValueCount: 1, valueLow: 0, valueHigh: 0, weight: 0, share: 0 }),
      ],
    }));
    render(<Holdings memberId="S1" />);
    const other = await screen.findByRole("button", { name: /^Other/ });
    expect(other).toHaveTextContent("2 assets · not charted · 1 none at year end · 1 no value stated");
    expect(other).toHaveTextContent("—");
  });

  it("describes an unreadable electronic report as such, not as a paper filing", async () => {
    fetchSenatorHoldings.mockResolvedValue(holdings({
      parsed: false, unreadableReason: "unrecognized", categories: [], holdings: [], holdingsCount: 0, total: 0,
    }));
    render(<Holdings memberId="S1" />);
    expect(await screen.findByText(/isn't in a layout that can be read automatically/)).toBeInTheDocument();
    expect(screen.queryByText(/paper/)).not.toBeInTheDocument();
    // Nothing to list, so no toggle.
    expect(screen.queryByRole("button", { name: /INVESTMENTS & ASSETS/ })).not.toBeInTheDocument();
  });

  it("dates a new-filer report's zero values to its own date, not a year end", async () => {
    fetchSenatorHoldings.mockResolvedValue(holdings({
      reportLabel: "new-filer report as of 2026-03-24", asOfDate: "2026-03-24",
      categories: [category({ zeroValueCount: 1, share: 1 })],
    }));
    render(<Holdings memberId="S1" />);
    expect(await screen.findByText(/1 none on 2026-03-24/)).toBeInTheDocument();
    expect(screen.queryByText(/year end/)).not.toBeInTheDocument();
  });

  it("names an undated filing made after the charted report instead of implying this one is the latest", async () => {
    fetchSenatorHoldings.mockResolvedValue(holdings({
      laterFilingLabel: "annual report filed 2026-08-12", laterFilingUrl: "https://efd.example/paper/x/",
    }));
    render(<Holdings memberId="S1" />);
    expect(await screen.findByText(/Also filed after this report: the annual report filed 2026-08-12/))
      .toBeInTheDocument();
    expect(screen.getByRole("link", { name: /VIEW THAT FILING/ })).toHaveAttribute("href", "https://efd.example/paper/x/");
  });
});

describe("Holdings — clicks while a request is in flight", () => {
  it("a second click on the same row clears the selection instead of re-selecting it", async () => {
    fetchSenatorHoldings.mockResolvedValueOnce(holdings());
    render(<Holdings memberId="S1" />);
    const stocks = await screen.findByRole("button", { name: /^Stocks/ });

    let resolveFirst: (value: HoldingsData) => void = () => {};
    fetchSenatorHoldings.mockReturnValueOnce(new Promise<HoldingsData>((r) => (resolveFirst = r)));
    fetchSenatorHoldings.mockResolvedValueOnce(holdings());
    await userEvent.click(stocks);
    await userEvent.click(stocks);

    expect(fetchSenatorHoldings.mock.calls.map((call) => call[1].category)).toEqual([null, "STOCKS", null]);
    resolveFirst(holdings({ categoryFilter: "STOCKS" }));
    // The superseded response never lands: the list stays unfiltered.
    await waitFor(() => expect(screen.getByRole("button", { name: /^Stocks/ })).toHaveAttribute("aria-pressed", "false"));
  });
});

describe("Holdings — paging while a filter request is in flight", () => {
  it("disables the pager, whose pages describe the list being replaced", async () => {
    fetchSenatorHoldings.mockResolvedValueOnce(holdings({ totalPages: 2, total: 20 }));
    render(<Holdings memberId="S1" />);
    const stocks = await screen.findByRole("button", { name: /^Stocks/ });

    fetchSenatorHoldings.mockReturnValueOnce(new Promise<HoldingsData>(() => {})); // never resolves
    await userEvent.click(stocks); // opens the list; request pending

    expect(screen.getByRole("button", { name: "Next page" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Previous page" })).toBeDisabled();
  });

  it("renders nothing while the first response is outstanding", () => {
    fetchSenatorHoldings.mockReturnValueOnce(new Promise<HoldingsData>(() => {}));
    const { container } = render(<Holdings memberId="S1" />);
    expect(container).toBeEmptyDOMElement();
  });
});
