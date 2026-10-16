import { afterEach, describe, expect, it } from "vitest";
import { render, screen, within } from "@testing-library/react";
import axe from "axe-core";
import ClosingSoon from "./ClosingSoon";
import { YOUR_STATE_KEY } from "@/hooks/useYourState";
import type { RegistrationClosing } from "@/types/thisWeek";

function row(over: Partial<RegistrationClosing>): RegistrationClosing {
  return {
    state: "MI",
    stateName: "Michigan",
    sentence: "Register online by Mon, Oct 19",
    closesOn: "2026-10-19",
    daysLeft: 3,
    countdown: "3 days",
    sameDay: "From 14 days before, register at your clerk with proof of residency.",
    register: {
      url: "https://mvic.sos.example.gov/",
      publisher: "Michigan Secretary of State",
      confirmed: false,
      statePage: true,
    },
    ...over,
  };
}

describe("ClosingSoon", () => {
  it("groups states by the day registration closes, each with where to register", async () => {
    render(
      <main>
        <ClosingSoon
          rows={[
            row({
              state: "CT",
              stateName: "Connecticut",
              closesOn: "2026-10-16",
              daysLeft: 0,
              sameDay: null,
            }),
            row({}),
            row({ state: "CA", stateName: "California", sameDay: null }),
          ]}
        />
      </main>
    );
    expect(screen.getByRole("heading", { name: "Registration closes soon" })).toBeInTheDocument();
    expect(
      screen.getByText("In 3 states, registration closes in the next 10 days.")
    ).toBeInTheDocument();
    const days = screen.getAllByRole("listitem").filter((li) => li.parentElement?.tagName === "OL");
    expect(days).toHaveLength(2);
    expect(days[0]).toHaveTextContent("Closes today");
    expect(days[1]).toHaveTextContent("Mon, Oct 19 · 3 days left");
    expect(within(days[1]).getByRole("link", { name: "Michigan" })).toHaveAttribute(
      "href",
      "/elections/states/MI#voter-deadlines"
    );
    expect(
      within(days[1]).getAllByRole("link", {
        name: /Register: Michigan Secretary of State, unconfirmed/,
      })
    ).toHaveLength(2);
    expect(days[1]).toHaveTextContent("After that: From 14 days before");
    const r = await axe.run(document.body, { rules: { "color-contrast": { enabled: false } } });
    expect(r.violations.map((v) => v.id)).toEqual([]);
  });

  it("says so when no state's registration closes soon", () => {
    render(<ClosingSoon rows={[]} />);
    expect(
      screen.getByText(/No state's regular registration closes in the next 10 days\./)
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Every state's deadlines" })).toHaveAttribute(
      "href",
      "/elections/calendar"
    );
  });

  it("keeps to names and links when compact", () => {
    render(<ClosingSoon rows={[row({})]} compact />);
    expect(screen.queryByText(/Register online by/)).not.toBeInTheDocument();
    expect(screen.queryByText(/After that/)).not.toBeInTheDocument();
  });
});

describe("ClosingSoon: the header's state", () => {
  afterEach(() => sessionStorage.clear());

  it("puts the reader's state first when it is closing soon", () => {
    sessionStorage.setItem(YOUR_STATE_KEY, "MI");
    render(<ClosingSoon rows={[row({ state: "CA", stateName: "California" }), row({})]} />);
    const line = screen.getByText(/^Your state/).closest("p")!;
    expect(line).toHaveTextContent(
      "Your state · Michigan: Register online by Mon, Oct 19 (3 days left)"
    );
    expect(
      within(line).getByRole("link", { name: /^Register: Michigan Secretary of State/ })
    ).toBeTruthy();
  });

  it("says when the reader's state isn't among them, and links its deadlines", () => {
    sessionStorage.setItem(YOUR_STATE_KEY, "TX");
    render(<ClosingSoon rows={[row({})]} />);
    expect(screen.getByText(/Texas isn't among them\./)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Texas's deadlines" })).toHaveAttribute(
      "href",
      "/elections/states/TX#voter-deadlines"
    );
  });

  it("says nothing extra without a chosen state", () => {
    render(<ClosingSoon rows={[row({})]} />);
    expect(screen.queryByText(/^Your state/)).not.toBeInTheDocument();
  });
});
