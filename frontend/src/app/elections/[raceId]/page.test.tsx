import { describe, expect, it, vi, beforeEach } from "vitest";

const redirect = vi.hoisted(() =>
  vi.fn((url: string) => {
    throw new Error(`REDIRECT:${url}`);
  })
);
const notFound = vi.hoisted(() =>
  vi.fn(() => {
    throw new Error("NOT_FOUND");
  })
);
vi.mock("next/navigation", () => ({ redirect, notFound }));
vi.mock("next/server", () => ({ connection: vi.fn(async () => undefined) }));

import RaceDetailRedirect from "./page";

// This route no longer renders its own page (2026-08: race detail merged
// into the state ballot page) — it exists only so links already published
// under /elections/{raceId} (e.g. past Bluesky posts) keep working.
describe("RaceDetailRedirect", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.stubGlobal("fetch", vi.fn());
  });

  it("redirects to the race's section of its state ballot page", async () => {
    (global.fetch as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: true,
      json: async () => ({ id: "2026-HOUSE-GA-06", state: "GA" }),
    });

    await expect(
      RaceDetailRedirect({ params: Promise.resolve({ raceId: "2026-HOUSE-GA-06" }) })
    ).rejects.toThrow("REDIRECT:/elections/states/GA?race=2026-HOUSE-GA-06#race-2026-HOUSE-GA-06");
  });

  it("sends a past election's race to its place in that election's archive", async () => {
    // After the results window the state page shows the next ballot, so an
    // old link (a Bluesky race post) lands on the archive instead.
    (global.fetch as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: true,
      json: async () => ({ id: "2026-HOUSE-GA-6", state: "GA", archiveCycle: 2026 }),
    });

    await expect(
      RaceDetailRedirect({ params: Promise.resolve({ raceId: "2026-HOUSE-GA-6" }) })
    ).rejects.toThrow("REDIRECT:/elections/2026#race-2026-HOUSE-GA-6");
  });

  it("keeps the site's own election's races on the state page", async () => {
    (global.fetch as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: true,
      json: async () => ({ id: "2026-SEN-GA", state: "GA", archiveCycle: null }),
    });

    await expect(
      RaceDetailRedirect({ params: Promise.resolve({ raceId: "2026-SEN-GA" }) })
    ).rejects.toThrow("REDIRECT:/elections/states/GA?race=2026-SEN-GA#race-2026-SEN-GA");
  });

  it("treats a four-digit year as an election, not a race", async () => {
    (global.fetch as ReturnType<typeof vi.fn>).mockResolvedValue({ ok: false, status: 404 });

    // The page hands a year to the archive page, which 404s an election
    // with no archive.
    const page = (await RaceDetailRedirect({
      params: Promise.resolve({ raceId: "2024" }),
    })) as unknown as { type: (p: unknown) => Promise<unknown>; props: unknown };
    await expect(page.type(page.props)).rejects.toThrow("NOT_FOUND");
    const url = String((global.fetch as ReturnType<typeof vi.fn>).mock.calls[0][0]);
    expect(url).toContain("/api/elections/archive/2024");
    expect(redirect).not.toHaveBeenCalled();
  });

  it("404s when the race no longer exists", async () => {
    (global.fetch as ReturnType<typeof vi.fn>).mockResolvedValue({ ok: false, status: 404 });

    await expect(
      RaceDetailRedirect({ params: Promise.resolve({ raceId: "gone" }) })
    ).rejects.toThrow("NOT_FOUND");
  });

  it("is an error, not a 404, when the backend can't be reached", async () => {
    // A 404 would tell a crawler (and a reader following a published link)
    // that the race is gone, for as long as the backend was down.
    (global.fetch as ReturnType<typeof vi.fn>).mockRejectedValue(new Error("network down"));
    await expect(RaceDetailRedirect({ params: Promise.resolve({ raceId: "x" }) })).rejects.toThrow(
      "network down"
    );

    (global.fetch as ReturnType<typeof vi.fn>).mockResolvedValue({ ok: false, status: 502 });
    await expect(RaceDetailRedirect({ params: Promise.resolve({ raceId: "x" }) })).rejects.toThrow(
      "HTTP 502"
    );
    expect(notFound).not.toHaveBeenCalled();
  });
});
