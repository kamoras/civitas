import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import Footer from "./Footer";
import { BAND } from "@/components/home/SectionsBand";

afterEach(cleanup);

describe("Footer", () => {
  it("lists every section's pages, except on a page that already lists them (the homepage)", () => {
    render(<Footer />);
    expect(screen.getByRole("link", { name: "Leaderboard" })).toBeInTheDocument();
    cleanup();
    render(<Footer omit={BAND} />);
    expect(screen.queryByRole("link", { name: "Leaderboard" })).toBeNull();
    // The site's own pages stay.
    expect(screen.getByRole("link", { name: "Changelog" })).toBeInTheDocument();
  });
});
