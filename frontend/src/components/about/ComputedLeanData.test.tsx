import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import ComputedLeanData from "./ComputedLeanData";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("ComputedLeanData", () => {
  it("offers both downloads and cites the table's own date", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({ asOf: "2026-10-15", sha256: "a".repeat(64) }),
      })
    );
    render(await ComputedLeanData());
    expect(screen.getByRole("link", { name: "CSV" }).getAttribute("href")).toBe(
      "/api/elections/district-lean.csv"
    );
    expect(screen.getByRole("link", { name: "JSON" }).getAttribute("href")).toBe(
      "/api/public/v1/district-lean"
    );
    expect(await screen.findByText(/\(2026, October 15\)\. Computed partisan lean/)).toBeTruthy();
    expect(screen.getByText("a".repeat(64))).toBeTruthy();
  });

  it("keeps the links when the backend can't be reached", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("down")));
    render(await ComputedLeanData());
    expect(screen.getByRole("link", { name: "CSV" })).toBeTruthy();
    expect(await screen.findByText(/\(n\.d\.\)/)).toBeTruthy();
  });
});
