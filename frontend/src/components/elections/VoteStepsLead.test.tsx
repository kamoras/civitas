import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import type { VoterCalendar, VoterDeadline } from "@/types/election";
import { VoteStepsLead } from "./VoteSteps";

const deadline = (over: Partial<VoterDeadline>): VoterDeadline =>
  ({
    kind: "registration",
    method: "online",
    basis: null,
    label: "Online registration",
    rule: "8 days before the election.",
    date: "2026-10-26",
    status: "open",
    daysLeft: 1,
    countdown: "tomorrow",
    sentence: "Online registration closes Oct 21",
    closesOn: "2026-10-21",
    source: { publisher: "Secretary of State", url: "https://sos.example.gov", type: "official" },
    verification: { status: "verified", checkedOn: "2026-10-13" },
    ...over,
  }) as VoterDeadline;

const calendar = (over: Partial<VoterCalendar>): VoterCalendar => ({
  timeZone: "America/New_York",
  localToday: "2026-10-20",
  note: null,
  next: null,
  registrationSummary: null,
  items: [],
  ...over,
});

describe("VoteStepsLead", () => {
  it("keeps a deadline that is tomorrow in view, marked, with a jump to the steps", () => {
    const d = deadline({});
    render(<VoteStepsLead calendar={calendar({ next: d, items: [d] })} hasSteps />);
    const lead = screen.getByTestId("vote-steps-lead");
    expect(lead).toHaveTextContent("Next deadline: Online registration closes Oct 21 · 1 day left");
    expect(lead.className).toMatch(/border-l-signal-orange/);
    expect(screen.getByRole("link")).toHaveAttribute("href", "#vote-steps");
  });

  it("says every deadline has passed rather than dropping them", () => {
    const d = deadline({ status: "closed", countdown: null, daysLeft: 0 });
    render(<VoteStepsLead calendar={calendar({ items: [d] })} hasSteps={false} />);
    expect(screen.getByTestId("vote-steps-lead")).toHaveTextContent(
      "Every voter deadline for this election has passed."
    );
    expect(screen.getByRole("link")).toHaveAttribute("href", "#voter-deadlines");
  });

  it("names no date when none could be read, and renders nothing with nothing to lead to", () => {
    const { container, rerender } = render(
      <VoteStepsLead calendar={calendar({ unread: "No dates read." })} hasSteps={false} />
    );
    expect(container).not.toHaveTextContent(/deadline:|passed/);
    rerender(<VoteStepsLead calendar={null} hasSteps={false} />);
    expect(container).toBeEmptyDOMElement();
  });
});
