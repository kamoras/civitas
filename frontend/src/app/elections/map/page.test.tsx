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

// Lighthouse can't hold the map to `is-crawlable` (.lighthouserc.populated.json):
// the backend shows the map only during the campaign, so which state the
// audit meets depends on the day CI runs. These hold the rule on every day.
describe("/elections/map crawlability", () => {
  beforeEach(() => vi.unstubAllGlobals());

  it("is indexable while the map is shown", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response(JSON.stringify({ cycle: 2026 }), { status: 200 }))
    );
    expect((await generateMetadata()).robots).not.toMatchObject({ index: false });
  });

  it("is noindex once the backend hides it (from election day)", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ detail: "The election map is shown during the campaign only." }), {
          status: 404,
        })
      )
    );
    expect((await generateMetadata()).robots).toMatchObject({ index: false });
  });
});
