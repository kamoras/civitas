import { describe, expect, it, vi } from "vitest";
import { afterEach } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import ElectionsPage from "./page";

const fetchPviMap = vi.hoisted(() => vi.fn());
vi.mock("@/lib/api", () => ({ fetchPviMap }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));
// RaceMap pulls in react-simple-maps and a topojson payload; the page's own
// behaviour is what is under test here, not the map's rendering.
vi.mock("@/components/elections/RaceMap", () => ({
  default: () => <div data-testid="race-map" />,
  FIPS_TO_STATE: { "13": "GA", "36": "NY", "11": "DC" },
}));
vi.mock("@/components/layout/Navbar", () => ({ default: () => <header /> }));
vi.mock("@/components/layout/Footer", () => ({ default: () => <footer /> }));
vi.mock("@/components/BackToTop", () => ({ default: () => null }));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("ElectionsPage", () => {
  it("still renders the directory when the payload carries no lean data", async () => {
    // A /pvi response missing `states` used to take the whole page down with
    // "Cannot read properties of undefined (reading 'AK')" — a white screen,
    // not a degraded map. `meta` on this same response is already documented
    // as possibly missing on older or cached backend responses, and nothing
    // validates the shape on the way in.
    fetchPviMap.mockResolvedValue({ districts: {}, cycleYear: 2026 });
    render(<ElectionsPage />);

    expect(await screen.findByRole("link", { name: /GA/ })).toBeInTheDocument();
    expect(screen.getByTestId("race-map")).toBeInTheDocument();
  });

  it("counts each lean direction for the map key", async () => {
    fetchPviMap.mockResolvedValue({ states: { GA: 3, NY: -10 }, districts: {}, cycleYear: 2026 });
    render(<ElectionsPage />);

    expect(await screen.findByText(/D-LEANING/)).toBeInTheDocument();
    expect(screen.getByText(/R-LEANING/)).toBeInTheDocument();
  });

  it("keeps DC out of the directory — it has no federal race to link to", async () => {
    fetchPviMap.mockResolvedValue({ states: {}, districts: {}, cycleYear: 2026 });
    render(<ElectionsPage />);

    await screen.findByTestId("race-map");
    expect(screen.queryByRole("link", { name: /^DC/ })).not.toBeInTheDocument();
  });

  it("surfaces a fetch failure instead of hanging on the loading line", async () => {
    fetchPviMap.mockRejectedValue(new Error("Failed to load election data"));
    render(<ElectionsPage />);

    expect(await screen.findByRole("alert")).toHaveTextContent("Failed to load election data");
  });
});
