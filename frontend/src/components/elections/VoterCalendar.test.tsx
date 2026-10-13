import { describe, expect, it } from "vitest";
import { render, screen, within } from "@testing-library/react";
import axe from "axe-core";
import VoterCalendar, { countdownText } from "./VoterCalendar";
import type { VoterCalendar as Calendar, VoterDeadline } from "@/types/election";

function item(over: Partial<VoterDeadline>): VoterDeadline {
  return {
    kind: "registration",
    method: "online",
    basis: null,
    label: "Online registration",
    rule: "Register online up to 8 days before Election Day.",
    date: "2026-10-26",
    endDate: null,
    time: null,
    endTime: null,
    daysBefore: 8,
    countedDate: "2026-10-26",
    adjustment: null,
    status: "open",
    daysLeft: 6,
    countdown: "6 days",
    sentence: "Online registration closes Oct 26",
    closesOn: "2026-10-26",
    source: {
      publisher: "Washington Secretary of State",
      url: "https://www.sos.wa.gov/x",
      type: "official",
    },
    verification: { status: "verified", checkedOn: "2026-10-13" },
    ...over,
  };
}

const closedMail = item({
  method: "mail",
  label: "Registration by mail",
  status: "closed",
  daysLeft: null,
  countdown: null,
  sentence: "Registration by mail closed Oct 5",
  closesOn: "2026-10-05",
  verification: { status: "unverified", checkedOn: "2026-10-13", detail: "blocked" },
});

const ballotReturn = item({
  kind: "ballot_return",
  method: "mail",
  basis: "postmarked",
  label: "Mail ballot return",
  sentence: "Mail ballots must be postmarked by Nov 3 and arrive by Nov 13",
  closesOn: "2026-11-03",
  daysLeft: 14,
  countdown: "14 days",
  source: {
    publisher: "Washington Secretary of State",
    url: "https://law.example/29A",
    type: "statute",
  },
  verification: { status: "statute", checkedOn: "2026-10-13" },
});

const calendar: Calendar = {
  timeZone: "America/Los_Angeles",
  localToday: "2026-10-20",
  note: "Every registered voter is mailed a ballot.",
  next: item({}),
  registrationSummary:
    "Registration by mail closed Oct 5. Still open: Online registration closes Oct 26 (6 days left).",
  items: [closedMail, item({}), ballotReturn],
};

describe("VoterCalendar", () => {
  it("leads with registration, keeps a passed deadline and says it closed", async () => {
    render(
      <main>
        <VoterCalendar calendar={calendar} stateName="Washington" />
      </main>
    );
    expect(
      screen.getByRole("heading", { name: "VOTER DEADLINES · WASHINGTON" })
    ).toBeInTheDocument();
    expect(screen.getByText(/^Registration by mail closed Oct 5\. Still open/)).toBeInTheDocument();
    // The next deadline is registration, already said: no second line.
    expect(screen.queryByText(/^Next deadline:/)).not.toBeInTheDocument();
    const list = screen.getByText("Every deadline, with its source").closest("details")!;
    const rows = within(list).getAllByRole("listitem", { hidden: true });
    expect(rows[0]).toHaveTextContent("CLOSEDRegistration by mail closed Oct 5");
    expect(rows[0]).toHaveTextContent("We could not confirm this page automatically.");
    expect(rows[1]).toHaveTextContent("6 days left");
    expect(rows[1]).toHaveTextContent("Register online up to 8 days before Election Day.");
    expect(
      within(rows[1]).getByRole("link", {
        name: "From Washington Secretary of State, checked Tue, Oct 13 (opens in new tab)",
        hidden: true,
      })
    ).toHaveAttribute("href", "https://www.sos.wa.gov/x");
    expect(rows[2]).toHaveTextContent("From state law, checked Tue, Oct 13");
    expect(document.querySelector("input, select, textarea")).toBeNull();
    const r = await axe.run(document.body, { rules: { "color-contrast": { enabled: false } } });
    expect(r.violations.map((v) => v.id)).toEqual([]);
  });

  it("names the next deadline when registration isn't it", () => {
    render(
      <VoterCalendar
        calendar={{ ...calendar, next: ballotReturn, registrationSummary: null }}
        stateName="Washington"
      />
    );
    expect(screen.getByText(/^Next deadline: Mail ballots must be postmarked/)).toHaveTextContent(
      "14 days left"
    );
  });

  it("says when every deadline has passed", () => {
    render(<VoterCalendar calendar={{ ...calendar, next: null }} stateName="Washington" />);
    expect(screen.getByText("Every deadline for this election has passed.")).toBeInTheDocument();
  });

  it("words a countdown for a deadline and for a window that hasn't opened", () => {
    expect(countdownText({ status: "open", countdown: "today" })).toBe("last day");
    expect(countdownText({ status: "open", countdown: "tomorrow" })).toBe("1 day left");
    expect(countdownText({ status: "upcoming", countdown: "6 days" })).toBe("in 6 days");
    expect(countdownText({ status: "upcoming", countdown: "tomorrow" })).toBe("opens tomorrow");
    expect(countdownText({ status: "closed", countdown: null })).toBeNull();
  });
});
