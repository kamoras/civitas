import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import Masthead, { electionLinks } from "./Masthead";
import { YOUR_STATE_KEY } from "@/hooks/useYourState";
import { __resetSessionTokenCache } from "@/hooks/useSessionToken";

afterEach(() => {
  cleanup();
  sessionStorage.clear();
  __resetSessionTokenCache();
});

describe("Masthead voter links", () => {
  it("lead to the pages where a state is chosen, and the page asks for nothing", () => {
    render(<Masthead />);
    const voters = screen.getByRole("navigation", { name: "For voters" });
    const href = (name: string) =>
      [...voters.querySelectorAll("a")].find((a) => a.textContent === name)!.getAttribute("href");
    expect(href("YOUR MEMBERS")).toBe("/politicians");
    expect(href("YOUR BALLOT")).toBe("/elections");
    expect(href("CONGRESS THIS WEEK")).toBe("/congress");
    // No form in the masthead: no field, no select, no button.
    expect(document.querySelector("input, select, textarea, button, form")).toBeNull();
  });
});

describe("Masthead with a state chosen in the header", () => {
  it("goes straight to that state's members and ballot", () => {
    sessionStorage.setItem(YOUR_STATE_KEY, "OH");
    render(<Masthead />);
    const voters = screen.getByRole("navigation", { name: "For voters" });
    const href = (name: string) =>
      [...voters.querySelectorAll("a")].find((a) => a.textContent === name)!.getAttribute("href");
    expect(href("YOUR MEMBERS")).toBe("/politicians?state=OH");
    expect(href("YOUR BALLOT")).toBe("/elections/states/OH");
  });
});

describe("Masthead election links", () => {
  it("follow the election's phase, and add nothing when it can't be read", () => {
    const labels = (phase: Parameters<typeof electionLinks>[0]) =>
      electionLinks(phase).map((l) => `${l.label} ${l.href}`);
    expect(labels("campaign")).toEqual([
      "SEAT MAP /elections/map?chamber=senate&view=usual",
      "NEW HOUSE MAPS /elections/redistricting",
    ]);
    expect(labels("results")).toEqual([
      "RESULTS /elections",
      "NEW HOUSE MAPS /elections/redistricting",
    ]);
    expect(labels("election_day")[0]).toBe("RESULTS /elections");
    expect(labels(null)).toEqual([]);
  });

  it("puts them in the voter line, and links the lede's election map", () => {
    render(<Masthead electionPhase="campaign" />);
    const voters = screen.getByRole("navigation", { name: "For voters" });
    expect([...voters.querySelectorAll("a")].map((a) => a.textContent)).toContain("SEAT MAP");
    expect(screen.getByRole("link", { name: "election map" })).toHaveAttribute(
      "href",
      "/elections/map?chamber=senate&view=usual"
    );
  });
});
