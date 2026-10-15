import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import axe from "axe-core";
import StateBallotNotFound from "@/app/elections/states/[state]/not-found";
import StateRedistrictingNotFound from "@/app/elections/redistricting/[state]/not-found";
import { stateFromSegment } from "./StateFromAddress";

vi.mock("@/components/layout/Navbar", () => ({ default: () => <header /> }));
vi.mock("@/components/layout/Footer", () => ({ default: () => <footer /> }));
const path = vi.hoisted(() => ({ current: "/elections/states/ohio" }));
vi.mock("next/navigation", () => ({ usePathname: () => path.current }));

afterEach(() => {
  path.current = "/elections/states/ohio";
});

const hrefs = () => screen.getAllByRole("link").map((a) => a.getAttribute("href"));

describe("state 404s", () => {
  it("reads a state from its code or name, and nothing else", () => {
    expect(stateFromSegment("oh")).toBe("OH");
    expect(stateFromSegment("Ohio")).toBe("OH");
    expect(stateFromSegment("new-york")).toBe("NY");
    expect(stateFromSegment("New%20Hampshire")).toBe("NH");
    expect(stateFromSegment("district_of_columbia")).toBe("DC");
    expect(stateFromSegment("ZZ")).toBeNull();
    expect(stateFromSegment("%E0%A4%A")).toBeNull();
  });

  it("offers the ballot of the state a mistyped address names, then the map", () => {
    render(<StateBallotNotFound />);
    expect(screen.getByRole("link", { name: /Ohio's ballot/ })).toHaveAttribute(
      "href",
      "/elections/states/OH"
    );
    expect(hrefs()).toEqual(["/elections/states/OH", "/elections", "/"]);
  });

  it("never links a missing ballot to itself", () => {
    path.current = "/elections/states/OH";
    render(<StateBallotNotFound />);
    expect(hrefs()).toEqual(["/elections", "/"]);
  });

  it("sends a state that didn't redraw to its ballot, the redrawn states and the map", () => {
    path.current = "/elections/redistricting/TX";
    render(<StateRedistrictingNotFound />);
    expect(hrefs()).toEqual([
      "/elections/states/TX",
      "/elections/redistricting",
      "/elections",
      "/",
    ]);
  });

  it("has no axe violations", async () => {
    render(<StateRedistrictingNotFound />);
    const result = await axe.run(document.body, {
      rules: { "color-contrast": { enabled: false } },
    });
    expect(result.violations.map((v) => v.id)).toEqual([]);
  });
});
