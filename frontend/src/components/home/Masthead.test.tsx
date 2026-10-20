import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import Masthead, { electionLinks } from "./Masthead";
import StateJump from "./StateJump";
import { ELECTION_MAP_HREF } from "@/lib/electionMap";

const push = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));

describe("Masthead", () => {
  it("states the promise and its terms as one plain line, and asks for nothing", () => {
    render(<Masthead />);
    expect(
      screen.getByRole("heading", {
        level: 1,
        name: "Who holds power, who pays for it, how they vote.",
      })
    ).toBeInTheDocument();
    const terms = screen.getByText(/^No party money\. No PAC money\./);
    expect(terms.className).not.toMatch(/rotate|border/);
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
  });

  it("adds the season's election pages", () => {
    render(<Masthead electionPhase="campaign" />);
    expect(screen.getByRole("link", { name: "Seat map" })).toHaveAttribute(
      "href",
      ELECTION_MAP_HREF
    );
    expect(electionLinks(null)).toEqual([]);
  });
});

describe("StateJump", () => {
  it("goes to the picked state's page, remembering nothing", async () => {
    render(<StateJump label="State" to="members" />);
    await userEvent.selectOptions(screen.getByLabelText("State"), "OR");
    expect(push).toHaveBeenCalledWith("/politicians?state=OR");
    expect(window.localStorage.length + window.sessionStorage.length).toBe(0);
  });
});
