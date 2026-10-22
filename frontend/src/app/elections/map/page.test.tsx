import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("next/server", () => ({ connection: vi.fn(async () => undefined) }));
vi.mock("@/components/layout/Navbar", () => ({ default: () => null }));
vi.mock("@/components/layout/Footer", () => ({ default: () => null }));
vi.mock("@/components/layout/SectionTabs", () => ({ default: () => null }));

import ElectionMapPage, { generateMetadata } from "./page";

const page = async () =>
  render(await ElectionMapPage({ searchParams: Promise.resolve({ chamber: "senate" }) }));

describe("/elections/map with no backend", () => {
  beforeEach(() => vi.unstubAllGlobals());

  it("says the map could not be reached, in the page, instead of throwing a 500", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("fetch failed")));
    await page();
    expect(screen.getByText(/could not be reached just now/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Elections/ })).toHaveAttribute("href", "/elections");
    // An outage is not a reason for a crawler to drop the page.
    expect((await generateMetadata()).robots).not.toMatchObject({ index: false });
  });

  it("says the same for a backend error", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("oops", { status: 502 })));
    await page();
    expect(screen.getByText(/could not be reached just now/)).toBeInTheDocument();
  });

  it("keeps the backend's own words for a map not built yet", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ detail: "The election map has not been built yet." }), {
          status: 503,
        })
      )
    );
    await page();
    expect(screen.getByText("The election map has not been built yet.")).toBeInTheDocument();
  });
});
