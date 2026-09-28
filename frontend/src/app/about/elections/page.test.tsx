import { describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import ElectionsChapter from "./page";
import { ACTION_CENTER_HREF } from "@/lib/routes";

vi.mock("@/components/layout/Navbar", () => ({ default: () => <header /> }));
vi.mock("@/components/layout/Footer", () => ({ default: () => <footer /> }));
vi.mock("next/navigation", () => ({ usePathname: () => "/about/elections" }));

describe("the elections methodology chapter", () => {
  it("links the Action Center by its named-tab href and doesn't claim every moment is posted", () => {
    render(<ElectionsChapter />);
    const section = document.getElementById("election-night")!;
    expect(within(section).getByRole("link", { name: "Action Center" })).toHaveAttribute(
      "href",
      ACTION_CENTER_HREF
    );
    expect(section).not.toHaveTextContent(/The same moments are posted/);
    expect(section).toHaveTextContent(/posts fewer moments than the feed shows/);
    expect(section).toHaveTextContent(/redrawn for this election/);
    expect(screen.getAllByRole("heading").length).toBeGreaterThan(0);
  });
});
