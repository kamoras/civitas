import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import StockTrades from "./StockTrades";
import type { StockTrade } from "@/types/senator";

const { base } = vi.hoisted(() => {
  const base: StockTrade = {
    ticker: null,
    assetName: "YELP INC",
    owner: "unknown",
    transactionType: "purchase",
    transactionDate: "2025-07-31",
    disclosureDate: "2026-06-29",
    daysToDisclose: null,
    late: null,
    amountLow: 15001,
    amountHigh: 50000,
    amountOpenEnded: false,
    industry: "UNCLASSIFIED",
    sourceUrl: "https://extapps2.oge.gov/a.pdf",
    parseConfidence: "text",
    reportKind: "annual",
  };
  return { base };
});

vi.mock("@/lib/api", () => ({
  fetchSenatorStockTrades: vi.fn(),
  fetchRepStockTrades: vi.fn(),
  fetchPresidentStockTrades: vi.fn().mockResolvedValue({
    trades: [
      base,
      { ...base, assetName: "BOND DUE 2038", reportKind: "periodic", parseConfidence: "ocr" },
      {
        ...base,
        assetName: "APPLE INC",
        reportKind: "periodic",
        daysToDisclose: 60,
        late: true,
        transactionDate: "2026-02-01",
      },
    ],
    total: 3,
    page: 1,
    perPage: 15,
    totalPages: 1,
    lateCount: 1,
  }),
}));

describe("StockTrades", () => {
  it("shows timeliness only where the date supports it", async () => {
    render(<StockTrades politicianId="trump-47" filer="president" />);
    await userEvent.click(await screen.findByRole("button", { name: /STOCK & CRYPTO TRADES/ }));
    expect(screen.getByText("ANNUAL REPORT")).toBeInTheDocument();
    expect(screen.getByText("READ FROM A SCAN")).toBeInTheDocument();
    // One timeliness badge: the text-read periodic trade. Neither the
    // annual report's row nor the scan's asserts on-time or late.
    expect(screen.getAllByText(/LATE DISCLOSURE|ON TIME/)).toHaveLength(1);
    expect(screen.getByText("LATE DISCLOSURE")).toBeInTheDocument();
  });
});
