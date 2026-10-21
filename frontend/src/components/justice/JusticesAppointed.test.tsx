import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import JusticesAppointed from "./JusticesAppointed";

const fetchJusticesAppointedBy = vi.fn();
vi.mock("@/lib/api", () => ({
  fetchJusticesAppointedBy: (id: string) => fetchJusticesAppointedBy(id),
}));

describe("JusticesAppointed", () => {
  it("links a justice with a page and names one without, with the seat and years", async () => {
    fetchJusticesAppointedBy.mockResolvedValue([
      {
        id: null,
        name: "Ada Early",
        seat: "Chief Justice",
        dateStart: "1902-02-01",
        dateEnd: "1930-01-01",
        isActive: false,
        photo: null,
      },
      {
        id: null,
        name: "Bo Brief",
        seat: "Associate Justice",
        dateStart: "1795-02-01",
        dateEnd: "1795-12-01",
        isActive: false,
        photo: null,
      },
      {
        id: "jane_q_example",
        name: "Jane Q. Example",
        seat: "Associate Justice",
        dateStart: "2006-01-31",
        dateEnd: null,
        isActive: true,
        photo: null,
      },
    ]);
    render(<JusticesAppointed presidentId="a-president-1" />);
    expect(await screen.findByRole("heading", { name: "Justices appointed" })).toBeInTheDocument();
    expect(fetchJusticesAppointedBy).toHaveBeenCalledWith("a-president-1");
    expect(screen.getByText("3 Supreme Court appointments, from the Federal Judicial Center."));
    expect(screen.getByRole("link", { name: /Jane Q\. Example.*since 2006/ })).toHaveAttribute(
      "href",
      "/politicians/jane_q_example"
    );
    // No page on the site: plain text, never a link to guess.
    expect(screen.queryByRole("link", { name: /Ada Early/ })).not.toBeInTheDocument();
    expect(screen.getByText("Chief Justice · 1902 to 1930")).toBeInTheDocument();
    expect(screen.getByText("Associate Justice · in 1795")).toBeInTheDocument();
  });

  it("renders nothing for a president who made no appointment", async () => {
    fetchJusticesAppointedBy.mockResolvedValue([]);
    const { container } = render(<JusticesAppointed presidentId="b-president-2" />);
    await Promise.resolve();
    expect(container).toBeEmptyDOMElement();
  });
});
