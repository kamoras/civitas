import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ActionPage from "./page";
import { ACTION_CENTER_HREF } from "@/lib/routes";

/* The tab bar writes every tab's URL through the History API, the issues
   tab included. A bare /action entry is what Next's client router cache
   later hands an in-app <Link href="/action"> back with (AGENTS.md,
   "Client-side URL state"), so the issues tab must name itself. */

vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(window.location.search),
  usePathname: () => window.location.pathname,
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}));
vi.mock("@/lib/api", () => ({
  fetchActionIssues: vi.fn(() => new Promise(() => {})),
  fetchOpenComments: vi.fn(() => new Promise(() => {})),
}));
vi.mock("@/components/layout/Navbar", () => ({ default: () => <header /> }));
vi.mock("@/components/layout/Footer", () => ({ default: () => <footer /> }));
vi.mock("@/components/BackToTop", () => ({ default: () => null }));
vi.mock("@/components/action/CivicTracker", () => ({
  default: () => null,
  LogActionButton: () => null,
}));
vi.mock("./TimelineTab", () => ({ default: () => <p>timeline</p> }));
vi.mock("./MonitorsTab", () => ({ default: () => <p>monitors</p> }));

afterEach(() => {
  cleanup();
  window.history.replaceState(null, "", "/");
});

describe("the Action Center tab bar", () => {
  it("names the issues tab in the URL when it is picked from another tab", async () => {
    window.history.replaceState(null, "", "/action?tab=timeline");
    render(<ActionPage />);
    await userEvent.click(await screen.findByRole("tab", { name: "ISSUES" }));
    expect(`${window.location.pathname}${window.location.search}`).toBe(ACTION_CENTER_HREF);
    expect(ACTION_CENTER_HREF).toBe("/action?tab=issues");
  });

  it("writes each other tab as ?tab=", async () => {
    window.history.replaceState(null, "", "/action?tab=issues");
    render(<ActionPage />);
    await userEvent.click(await screen.findByRole("tab", { name: "TIMELINE" }));
    expect(window.location.search).toBe("?tab=timeline");
  });
});
