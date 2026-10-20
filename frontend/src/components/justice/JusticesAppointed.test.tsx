import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import JusticesAppointed from "./JusticesAppointed";

const fetchJusticesAppointedBy = vi.fn();
vi.mock("@/lib/api", () => ({
  fetchJusticesAppointedBy: (id: string) => fetchJusticesAppointedBy(id),
}));

describe("JusticesAppointed", () => {
  it("lists each justice the president appointed, linked, sitting and former", async () => {
    fetchJusticesAppointedBy.mockResolvedValue([
      {
        id: "jane_q_example",
        name: "Jane Q. Example",
        roleTitle: "Associate Justice",
        dateStart: "2006-01-31",
        dateEnd: null,
        isActive: true,
        photo: null,
      },
      {
        id: "john_doe",
        name: "John Doe",
        roleTitle: "Associate Justice",
        dateStart: "1990-10-09",
        dateEnd: "2009-06-29",
        isActive: false,
        photo: null,
      },
    ]);
    render(<JusticesAppointed presidentId="a-president-1" />);
    expect(await screen.findByRole("heading", { name: "Justices appointed" })).toBeInTheDocument();
    expect(fetchJusticesAppointedBy).toHaveBeenCalledWith("a-president-1");
    expect(screen.getByRole("link", { name: /Jane Q\. Example.*since 2006/ })).toHaveAttribute(
      "href",
      "/politicians/jane_q_example"
    );
    expect(screen.getByText("Served 1990 to 2009")).toBeInTheDocument();
  });

  it("renders nothing for a president who appointed none on record", async () => {
    fetchJusticesAppointedBy.mockResolvedValue([]);
    const { container } = render(<JusticesAppointed presidentId="b-president-2" />);
    await Promise.resolve();
    expect(container).toBeEmptyDOMElement();
  });
});
