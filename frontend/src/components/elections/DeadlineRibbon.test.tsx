import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import DeadlineRibbon from "./DeadlineRibbon";
import type { VoterDeadline } from "@/types/election";

const deadline = (over: Partial<VoterDeadline>): VoterDeadline =>
  ({
    kind: "ballot_return",
    method: "mail",
    basis: "received",
    label: "",
    rule: "",
    date: "2026-10-20",
    endDate: null,
    status: "open",
    closesOn: "2026-10-20",
    sentence: "Mail ballots must arrive by October 20.",
    source: {
      publisher: "Secretary of State",
      url: "https://sos.example.gov/vote",
      type: "official",
    },
    ...over,
  }) as VoterDeadline;

describe("DeadlineRibbon", () => {
  it("ends a window that runs past election day at the track's end", () => {
    const { container } = render(
      <DeadlineRibbon
        items={[deadline({ date: "2026-10-14", endDate: "2026-11-13" })]}
        today="2026-10-16"
        electionDay="2026-11-03"
        registerBy={null}
      />
    );
    for (const band of container.querySelectorAll<HTMLElement>("span[style*='width']")) {
      const right = parseFloat(band.style.left) + parseFloat(band.style.width);
      expect(right).toBeLessThanOrEqual(100.0001);
    }
  });

  it("names each deadline from the keyboard, linked to its source", async () => {
    render(
      <DeadlineRibbon
        items={[
          deadline({
            label: "Register",
            rule: "15 days before Election Day",
            kind: "registration",
          }),
          deadline({ label: "Return a mail ballot", date: "2026-10-30", closesOn: "2026-10-30" }),
        ]}
        today="2026-10-16"
        electionDay="2026-11-03"
        registerBy={null}
      />
    );
    await userEvent.tab();
    expect(
      screen.getByRole("group", { name: /^Voter deadlines, to election day: 2 marks/ })
    ).toHaveFocus();
    await userEvent.keyboard("{ArrowRight}");
    expect(
      screen.getByText(
        "Register: Mail ballots must arrive by October 20, 15 days before Election Day."
      )
    ).toBeInTheDocument();
    expect(document.querySelector("a[aria-hidden='true']")).toHaveAttribute(
      "href",
      "https://sos.example.gov/vote"
    );
  });
});
