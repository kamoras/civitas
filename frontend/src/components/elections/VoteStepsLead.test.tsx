import { describe, expect, it } from "vitest";
import { render, screen, within } from "@testing-library/react";
import type { VoteStep, VoterCalendar, VoterDeadline } from "@/types/election";
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

const step = (task: VoteStep["task"], over: Partial<VoteStep> = {}): VoteStep => ({
  task,
  url: `https://vote.example.gov/${task}`,
  host: "vote.example.gov",
  sourceName: "Example Secretary of State",
  isStateSpecific: true,
  unconfirmed: false,
  fallback: null,
  deadline: null,
  ...over,
});

describe("VoteStepsLead", () => {
  it("keeps a deadline that is tomorrow in view, marked, with a jump to the steps", () => {
    const d = deadline({});
    render(
      <VoteStepsLead calendar={calendar({ next: d, items: [d] })} steps={[step("register")]} />
    );
    const lead = screen.getByTestId("vote-steps-lead");
    // One line (#1160's form): the calendar's sentence, the days left as a
    // figure beside it, the row marked when the deadline is a day off.
    expect(lead).toHaveTextContent("Next deadline: Online registration closes Oct 21.");
    const figure = within(lead).getByRole("img", { name: "1 day left." });
    expect(figure.closest("div.border-l-signal-orange")).toBeTruthy();
    expect(within(lead).getByRole("link", { name: /step by step/ })).toHaveAttribute(
      "href",
      "#vote-steps"
    );
  });

  it("leads with the official sample-ballot lookup as a button, and never registration", () => {
    // #1123: a reader who couldn't find the ballot lookup clicked register.
    render(
      <VoteStepsLead
        calendar={null}
        steps={[step("check_registration"), step("register"), step("sample_ballot")]}
      />
    );
    const button = screen.getByTestId("ballot-lookup");
    expect(button).toHaveAttribute("href", "https://vote.example.gov/sample_ballot");
    expect(button).toHaveAccessibleName(
      "See your sample ballot: vote.example.gov · Example Secretary of State (opens in new tab)"
    );
    expect(button).toHaveTextContent("Opens vote.example.gov · Example Secretary of State");
    expect(button.className).toMatch(/min-h-11/);
    const lead = screen.getByTestId("vote-steps-lead");
    expect(within(lead).queryByRole("link", { name: /regist/i })).not.toBeInTheDocument();
  });

  it("words a walled ballot lookup as the steps do, with its fallback", () => {
    render(
      <VoteStepsLead
        calendar={null}
        steps={[
          step("sample_ballot", {
            unconfirmed: true,
            fallback: {
              url: "https://www.usa.gov/election-office",
              sourceName: "USAGov",
              host: "usa.gov",
            },
          }),
        ]}
      />
    );
    expect(screen.getByTestId("ballot-lookup")).toHaveAccessibleName(/, unconfirmed \(opens/);
    expect(screen.getByTestId("vote-steps-lead")).toHaveTextContent(
      "This site blocks automated checks, so we can't confirm it's up. If it doesn't open, use usa.gov ↗"
    );
    expect(
      screen.getByRole("link", { name: "See your sample ballot through USAGov (opens in new tab)" })
    ).toHaveAttribute("href", "https://www.usa.gov/election-office");
  });

  it("names the county office through USAGov where the state shows no sample ballot", () => {
    render(
      <VoteStepsLead
        calendar={null}
        steps={[step("sample_ballot", { isStateSpecific: false, sourceName: "USAGov" })]}
      />
    );
    expect(screen.getByTestId("ballot-lookup")).toHaveTextContent(
      "Opens your county election office, through USAGov"
    );
  });

  it("says plainly who publishes sample ballots where the state has no statewide lookup", () => {
    render(
      <VoteStepsLead
        calendar={null}
        steps={[
          step("sample_ballot", {
            isStateSpecific: false,
            localOffice: "city or town",
            url: "https://www.usa.gov/election-office",
            host: "usa.gov",
            sourceName: "USAGov's directory of election offices",
          }),
        ]}
      />
    );
    const button = screen.getByTestId("ballot-lookup");
    expect(button).toHaveTextContent(
      "Your city or town publishes sample ballots: find your city or town election office"
    );
    expect(button).toHaveTextContent("Opens usa.gov · USAGov's directory of election offices");
    expect(button).not.toHaveTextContent("See your sample ballot");
  });

  it("says every deadline has passed rather than dropping them", () => {
    const d = deadline({ status: "closed", countdown: null, daysLeft: 0 });
    render(<VoteStepsLead calendar={calendar({ items: [d] })} steps={[]} />);
    expect(screen.getByTestId("vote-steps-lead")).toHaveTextContent(
      "Every voter deadline for this election has passed"
    );
    expect(screen.getByRole("link", { name: /Every voter deadline/ })).toHaveAttribute(
      "href",
      "#voter-deadlines"
    );
    expect(screen.queryByTestId("ballot-lookup")).not.toBeInTheDocument();
  });

  it("names no date when none could be read, and renders nothing with nothing to lead to", () => {
    const { container, rerender } = render(
      <VoteStepsLead calendar={calendar({ unread: "No dates read." })} steps={null} />
    );
    expect(container).not.toHaveTextContent(/deadline:|passed/);
    rerender(<VoteStepsLead calendar={null} steps={null} />);
    expect(container).toBeEmptyDOMElement();
  });
});
