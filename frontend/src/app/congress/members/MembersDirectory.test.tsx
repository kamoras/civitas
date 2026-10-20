import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import MembersDirectory from "./MembersDirectory";
import type { PoliticianCard } from "@/types/politicians";

const api = vi.hoisted(() => ({
  fetchPoliticianDirectory: vi.fn(),
  fetchHouseDistrictsByCounty: vi.fn(),
}));
vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  ...api,
}));
let params = new URLSearchParams();
vi.mock("next/navigation", () => ({ useSearchParams: () => params }));
vi.mock("@/components/layout/Navbar", () => ({ default: () => <header /> }));
vi.mock("@/components/layout/SectionTabs", () => ({ default: () => <nav /> }));
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
  api.fetchHouseDistrictsByCounty.mockImplementation(async (state: string) => ({
    state,
    congress: 119,
    available: true,
    districts:
      state === "WY"
        ? [{ district: 0, counties: ["Albany"] }]
        : [
            { district: 1, counties: ["Hamilton"] },
            { district: 2, counties: ["Adams"] },
          ],
  }));
  return render(<MembersDirectory />);
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("MembersDirectory state picker", () => {
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
      name: /Find your election district on the Ohio ballot page/,
    });
    expect(link).toHaveAttribute("href", "/elections/states/OH#ballot-house");
    expect(screen.getByText(/Find your House member by city, town or county/)).toBeTruthy();
    expect(screen.queryByText(/20\d\d/)).toBeNull();
  });

  it("leaves the county finder out for a one-seat state", async () => {
    open("");
    await userEvent.selectOptions(
      await screen.findByRole("combobox", { name: "Filter by state" }),
      "WY"
    );
    await screen.findByText("1 member");
    expect(screen.queryByText(/Find your House member by city, town or county/)).toBeNull();
    expect(screen.queryByRole("link", { name: /Find your election district/ })).toBeNull();
  });
});

describe("MembersDirectory delegation order", () => {
  it("puts a picked state's senators before the House finder, and its House members after", async () => {
    open("state=OH");
    const finder = await screen.findByText(/Find your House member by city, town or county/);
    const senator = screen.getByRole("link", { name: /Senator · Ohio/ });
    const house = screen.getAllByRole("link", { name: /Representative · Ohio/ });
    const FOLLOWING = Node.DOCUMENT_POSITION_FOLLOWING;
    expect(senator.compareDocumentPosition(finder) & FOLLOWING).toBeTruthy();
    for (const h of house) expect(finder.compareDocumentPosition(h) & FOLLOWING).toBeTruthy();
  });
});

describe("MembersDirectory chambers", () => {
  it("lists members of Congress only, never the president or a justice", async () => {
    directory.push(
      card({
        id: "p1",
        branch: "president",
        role: "President (47th)",
      }),
      card({
        id: "j1",
        branch: "scotus",
        role: "Associate Justice",
      })
    );
    try {
      open("state=OH");
      await screen.findAllByRole("link", { name: /Senator · Ohio/ });
      expect(screen.queryByRole("link", { name: /President|Justice/ })).toBeNull();
      expect(screen.queryByRole("button", { name: /president|scotus/i })).toBeNull();
    } finally {
      directory.splice(-2);
    }
  });

  it("reads ?branch= for a chamber and ignores any other value", async () => {
    open("branch=senate");
    expect(screen.getByRole("button", { name: "Senate" })).toHaveAttribute("aria-pressed", "true");
    cleanup();
    open("branch=president");
    expect(screen.getByRole("button", { name: "Both chambers" })).toHaveAttribute(
      "aria-pressed",
      "true"
    );
  });
});
