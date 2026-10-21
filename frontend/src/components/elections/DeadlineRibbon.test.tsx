import { describe, expect, it } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
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

  it("puts each deadline's tick over the band it sits on, a 24px target", () => {
    const { container } = render(
      <DeadlineRibbon
        items={[
          deadline({
            label: "Early voting",
            kind: "early_voting",
            date: "2026-10-20",
            endDate: "2026-11-01",
          }),
          deadline({ label: "Request a mail ballot", date: "2026-10-27", closesOn: "2026-10-27" }),
        ]}
        today="2026-10-16"
        electionDay="2026-11-03"
        registerBy={null}
      />
    );
    const tick = container.querySelector<HTMLElement>(
      "[data-mark='ballot_return-mail-received-2026-10-27']"
    )!;
    expect(tick).toHaveClass("z-10", "w-6", "h-6");
    // A pointer on the tick, where the band runs under it, reads the tick.
    fireEvent.pointerMove(tick.firstElementChild!, { pointerType: "mouse" });
    expect(document.querySelector("[aria-hidden='true'].z-20")).toHaveTextContent(
      "Request a mail ballot"
    );
  });
});
