import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import axe from "axe-core";
import OnTheBallot from "./OnTheBallot";
import type { IssueBallot } from "@/types/action";

const GA: IssueBallot = {
  state: "GA",
  stateName: "Georgia",
  races: [{ id: "2026-SEN-GA", label: "Senate", candidate: "Roe" }],
  deadline: {
    kind: "registration",
    sentence: "Register online by Monday, October 19",
    closesOn: "2026-10-19",
    daysLeft: 3,
  },
  registerLink: {
    url: "https://georgia.example.gov/register",
    publisher: "Georgia Secretary of State",
    confirmed: true,
    statePage: true,
  },
  lookup: {
    url: "https://mvp.example.gov/",
    label: "My Voter Page",
    sourceName: "Georgia Secretary of State",
    isStateSpecific: true,
    verifiedAt: "2026-10-16",
  },
};

describe("OnTheBallot", () => {
  it("names the race, the deadline and the state's own links", async () => {
    render(
      <main>
        <OnTheBallot ballot={[GA]} />
      </main>
    );
    expect(screen.getByText("On the ballot")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Georgia" })).toHaveAttribute(
      "href",
      "/elections/states/GA"
    );
    expect(screen.getByRole("link", { name: "Senate race (Roe)" })).toHaveAttribute(
      "href",
      "/elections/states/GA#race-2026-SEN-GA"
    );
    expect(
      screen.getByRole("link", { name: "Register online by Monday, October 19 (3 days left)" })
    ).toHaveAttribute("href", "/elections/states/GA#voter-deadlines");
    expect(
      screen.getByRole("link", { name: /^Register: Georgia Secretary of State/ })
    ).toHaveAttribute("href", "https://georgia.example.gov/register");
    expect(
      screen.getByRole("link", { name: /Check your registration: Georgia's official lookup/ })
    ).toHaveAttribute("href", "https://mvp.example.gov/");
    const r = await axe.run(document.body, { rules: { "color-contrast": { enabled: false } } });
    expect(r.violations.map((v) => v.id)).toEqual([]);
  });

  it("says Voting in, not On the ballot, for a state with no race, and no register link once closed", () => {
    render(
      <OnTheBallot
        ballot={[
          {
            ...GA,
            state: "CA",
            stateName: "California",
            races: [],
            deadline: {
              kind: "early_voting",
              sentence: "Early voting ends Monday, November 2",
              closesOn: "2026-11-02",
              daysLeft: 17,
            },
            lookup: { ...GA.lookup!, isStateSpecific: false, sourceName: "USAGov" },
          },
        ]}
      />
    );
    expect(screen.getByText("Voting in")).toBeInTheDocument();
    expect(screen.queryByText("On the ballot")).toBeNull();
    expect(screen.queryByRole("link", { name: /^Register:/ })).toBeNull();
    expect(
      screen.getByRole("link", { name: /Find your election office: USAGov/ })
    ).toBeInTheDocument();
  });

  it("renders nothing without a ballot", () => {
    const { container } = render(<OnTheBallot ballot={undefined} />);
    expect(container).toBeEmptyDOMElement();
  });
});
