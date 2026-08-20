import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import PageMasthead from "./PageMasthead";

describe("PageMasthead", () => {
  it("gives every page exactly one h1", () => {
    render(<PageMasthead eyebrow="Bills · pipeline" title="Bills in motion" />);
    const headings = screen.getAllByRole("heading", { level: 1 });
    expect(headings).toHaveLength(1);
    expect(headings[0]).toHaveTextContent("Bills in motion");
  });

  it("keeps the eyebrow out of the heading, so it is not read as part of the title", () => {
    render(<PageMasthead eyebrow="Elections · partisan lean" title="Midterm ballot" />);
    expect(screen.getByRole("heading", { level: 1 })).not.toHaveTextContent("partisan lean");
    expect(screen.getByText("Elections · partisan lean")).toBeInTheDocument();
  });

  it("renders the standing line and the aside only when given them", () => {
    const { container, rerender } = render(<PageMasthead eyebrow="E" title="T" />);
    expect(container.querySelector("header")?.children).toHaveLength(1);

    rerender(
      <PageMasthead eyebrow="E" title="T" aside={<span>R+3</span>}>
        Standing line.
      </PageMasthead>
    );
    expect(screen.getByText("R+3")).toBeInTheDocument();
    expect(screen.getByText("Standing line.")).toBeInTheDocument();
  });

  it("carries the section rule every page relies on", () => {
    const { container } = render(<PageMasthead eyebrow="E" title="T" />);
    expect(container.querySelector("header")?.className).toContain("border-b-3");
  });
});
