import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import PoliticiansPage from "./page";
import type { PoliticianCard } from "@/types/politicians";

const api = vi.hoisted(() => ({ fetchPoliticianDirectory: vi.fn() }));
vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  ...api,
}));
let params = new URLSearchParams();
vi.mock("next/navigation", () => ({ useSearchParams: () => params }));
vi.mock("@/components/layout/Navbar", () => ({ default: () => <header /> }));
vi.mock("@/components/layout/Footer", () => ({ default: () => <footer /> }));
vi.mock("@/components/BackToTop", () => ({ default: () => null }));

const card = (over: Partial<PoliticianCard>): PoliticianCard => ({
  id: "x",
  branch: "house",
  name: "Pat Example",
  party: "D",
  role: "Representative",
  hasScorecard: false,
  activeIssueCount: 0,
  isCurrent: true,
  ...over,
});

const directory = [
  card({ id: "s1", branch: "senate", role: "Senator", state: "OH", stateName: "Ohio" }),
  card({ id: "h1", state: "OH", stateName: "Ohio", district: 1 }),
  card({ id: "h2", state: "OH", stateName: "Ohio", district: 2 }),
  card({ id: "h3", state: "WY", stateName: "Wyoming", district: 0 }),
];

function open(search: string) {
  params = new URLSearchParams(search);
  api.fetchPoliticianDirectory.mockResolvedValue(directory);
  return render(<PoliticiansPage />);
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("PoliticiansPage state picker", () => {
  it("names states in full, in alphabetical order of the name", () => {
    open("");
    const picker = screen.getByRole("combobox", { name: "Filter by state" });
    const names = Array.from(picker.querySelectorAll("option")).map((o) => o.textContent);
    expect(names).toContain("Ohio");
    // By name, not by postal code: Maine (ME) before Maryland (MD).
    expect(names.indexOf("Maine")).toBeLessThan(names.indexOf("Maryland"));
    expect(picker.querySelector('option[value="OH"]')?.textContent).toBe("Ohio");
  });

  it("offers the county finder, without a stale cycle year, where there is a district to find", async () => {
    open("state=OH");
    const link = await screen.findByRole("link", {
      name: /Find it by county on the Ohio ballot page/,
    });
    expect(link).toHaveAttribute("href", "/elections/states/OH#ballot-house");
    expect(screen.queryByText(/2026 district lines/)).toBeNull();
  });

  it("leaves the county finder out for a one-seat state", async () => {
    open("");
    await userEvent.selectOptions(
      await screen.findByRole("combobox", { name: "Filter by state" }),
      "WY"
    );
    await screen.findByText("1 POLITICIAN");
    expect(screen.queryByRole("link", { name: /Find it by county/ })).toBeNull();
  });
});
