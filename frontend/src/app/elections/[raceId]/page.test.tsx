import { describe, expect, it, vi, beforeEach } from "vitest";

const redirect = vi.hoisted(() => vi.fn((url: string) => { throw new Error(`REDIRECT:${url}`); }));
const notFound = vi.hoisted(() => vi.fn(() => { throw new Error("NOT_FOUND"); }));
vi.mock("next/navigation", () => ({ redirect, notFound }));

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
    ).rejects.toThrow("REDIRECT:/elections/states/GA#race-2026-HOUSE-GA-06");
  });

  it("404s when the race no longer exists", async () => {
    (global.fetch as ReturnType<typeof vi.fn>).mockResolvedValue({ ok: false });

    await expect(
      RaceDetailRedirect({ params: Promise.resolve({ raceId: "gone" }) })
    ).rejects.toThrow("NOT_FOUND");
  });

  it("404s rather than throws when the backend fetch itself fails", async () => {
    (global.fetch as ReturnType<typeof vi.fn>).mockRejectedValue(new Error("network down"));

    await expect(
      RaceDetailRedirect({ params: Promise.resolve({ raceId: "x" }) })
    ).rejects.toThrow("NOT_FOUND");
  });
});
