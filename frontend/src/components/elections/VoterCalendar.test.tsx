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

const LOOKUP = { lookupHref: "https://voter.example.gov/lookup", lookupIsStateSpecific: true };

// The wide layout's copy of the calendar (a phone's folds most of it).
const wide = () => within(document.querySelector<HTMLElement>('[data-calendar="wide"]')!);

describe("VoterCalendar", () => {
  it("leads with registration, keeps a passed deadline and says it closed", async () => {
    render(
      <main>
        <VoterCalendar calendar={calendar} stateName="Washington" {...LOOKUP} />
      </main>
    );
    expect(
      screen.getByRole("heading", { name: "Voter deadlines · Washington" })
    ).toBeInTheDocument();
    expect(wide().getByText(/^Registration by mail closed Oct 5\. Still open/)).toBeInTheDocument();
    // The next deadline is registration, already said: no second line.
    expect(wide().queryByText(/^Next deadline:/)).not.toBeInTheDocument();
    const list = wide().getByText("Every deadline, with its source").closest("details")!;
    const rows = within(list).getAllByRole("listitem", { hidden: true });
    expect(rows[0]).toHaveTextContent("ClosedRegistration by mail closed Oct 5");
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
        {...LOOKUP}
      />
    );
    expect(wide().getByText(/^Next deadline: Mail ballots must be postmarked/)).toHaveTextContent(
      "14 days left"
    );
  });

  it("says when every deadline has passed", () => {
    render(
      <VoterCalendar calendar={{ ...calendar, next: null }} stateName="Washington" {...LOOKUP} />
    );
    expect(wide().getByText("Every deadline for this election has passed.")).toBeInTheDocument();
  });

  it("lists no dates for a state read from no official source, and points to its lookup", async () => {
    render(
      <main>
        <VoterCalendar
          calendar={{
            ...calendar,
            next: null,
            registrationSummary: null,
            note: null,
            items: [],
            unread:
              "We couldn't read New York's voter deadlines from its election board, so we don't list any.",
          }}
          stateName="New York"
          {...LOOKUP}
        />
      </main>
    );
    const section = screen.getByRole("region", { name: "Voter deadlines · New York" });
    expect(section).toHaveTextContent(
      "We couldn't read New York's voter deadlines from its election board, so we don't list any. Check your deadlines with your county board of elections"
    );
    expect(
      within(section).getByRole("link", {
        name: "New York's official voter lookup (opens in new tab)",
      })
    ).toHaveAttribute("href", "https://voter.example.gov/lookup");
    // No dates, no countdowns, no list.
    expect(section.textContent).not.toMatch(/Oct|Nov|days left|Every deadline/);
    const r = await axe.run(document.body, { rules: { "color-contrast": { enabled: false } } });
    expect(r.violations.map((v) => v.id)).toEqual([]);
  });

  it("words a countdown for a deadline and for a window that hasn't opened", () => {
    expect(countdownText({ status: "open", countdown: "today" })).toBe("last day");
    expect(countdownText({ status: "open", countdown: "tomorrow" })).toBe("1 day left");
    expect(countdownText({ status: "upcoming", countdown: "6 days" })).toBe("in 6 days");
    expect(countdownText({ status: "upcoming", countdown: "tomorrow" })).toBe("opens tomorrow");
    expect(countdownText({ status: "closed", countdown: null })).toBeNull();
  });
});

describe("VoterCalendar: registering", () => {
  const register = {
    url: "https://olvr.sos.example.gov/",
    publisher: "Washington Secretary of State",
    confirmed: true,
    statePage: true,
  };

  it("leads with the deadline, its countdown and where to register, then draws the ribbon", async () => {
    render(
      <main>
        <VoterCalendar
          calendar={{
            ...calendar,
            electionDay: "2026-11-03",
            register,
            registration: {
              status: "open",
              sentence: "Register online by Mon, Oct 26",
              closesOn: "2026-10-26",
              daysLeft: 6,
              countdown: "6 days",
              sameDay: "Register in person at a voting center through Election Day.",
              source: item({}).source,
            },
          }}
          stateName="Washington"
          {...LOOKUP}
        />
      </main>
    );
    expect(wide().getByText("Register online by Mon, Oct 26")).toHaveTextContent("6 days left");
    expect(
      wide().getByText("After that: Register in person at a voting center through Election Day.")
    ).toBeInTheDocument();
    expect(
      wide().getByRole("link", {
        name: "Register to vote: Washington Secretary of State (opens in new tab)",
      })
    ).toHaveAttribute("href", register.url);
    // The open registration row links it too.
    expect(
      wide().getAllByRole("link", { name: /^Register: Washington Secretary of State/ })
    ).toHaveLength(1);
    // The list is open by default: it is the ribbon's text equivalent.
    expect(wide().getByText("Every deadline, with its source").closest("details")).toHaveAttribute(
      "open"
    );
    expect(document.querySelector('[data-calendar="wide"] [aria-hidden="true"]')).toHaveTextContent(
      "Register by Mon, Oct 26"
    );
    const r = await axe.run(document.body, { rules: { "color-contrast": { enabled: false } } });
    expect(r.violations.map((v) => v.id)).toEqual([]);
  });

  it("folds everything after the registration line behind one disclosure on a phone", () => {
    // Mobile spec #5: the whole calendar put the contests 2,555px down.
    render(
      <VoterCalendar
        calendar={{
          ...calendar,
          electionDay: "2026-11-03",
          register,
          registration: {
            status: "open",
            sentence: "Register online by Mon, Oct 26",
            closesOn: "2026-10-26",
            daysLeft: 6,
            countdown: "6 days",
            sameDay: "Register in person at a voting center through Election Day.",
            source: item({}).source,
          },
        }}
        stateName="Washington"
        {...LOOKUP}
      />
    );
    const phone = document.querySelector<HTMLElement>('[data-calendar="phone"]')!;
    expect(phone.className).toMatch(/\blg:hidden\b/);
    expect(within(phone).getByText("Register online by Mon, Oct 26")).toHaveTextContent(
      "6 days left"
    );
    expect(
      within(phone).getByRole("link", {
        name: "Register to vote: Washington Secretary of State (opens in new tab)",
      })
    ).toBeInTheDocument();
    const fold = within(phone).getByText("Every deadline, with its source").closest("details")!;
    expect(fold.open).toBe(false);
    // Still quoted, with each rule and source, one tap away (§7).
    expect(fold).toHaveTextContent("After that: Register in person at a voting center");
    expect(fold).toHaveTextContent("Register online up to 8 days before Election Day.");
    expect(within(fold).getAllByRole("listitem", { hidden: true })).toHaveLength(3);
  });

  it("says plainly that North Dakota has no registration, with no register link", () => {
    render(
      <VoterCalendar
        calendar={{
          ...calendar,
          register: null,
          registration: {
            status: "none",
            sentence: "North Dakota does not have voter registration.",
            closesOn: null,
            daysLeft: null,
            countdown: null,
            sameDay: null,
            source: null,
          },
        }}
        stateName="North Dakota"
        {...LOOKUP}
      />
    );
    expect(wide().getByText("North Dakota does not have voter registration.")).toBeInTheDocument();
    expect(wide().queryByRole("link", { name: /^Register/ })).not.toBeInTheDocument();
  });

  it("words same-day registration and an unconfirmed vote.gov link as such", () => {
    render(
      <VoterCalendar
        calendar={{
          ...calendar,
          register: {
            url: "https://vote.gov/",
            publisher: "Vote.gov",
            confirmed: false,
            statePage: false,
          },
          registration: {
            status: "same_day",
            sentence: "Register at your polling place on Election Day and vote.",
            closesOn: "2026-11-03",
            daysLeft: 14,
            countdown: "14 days",
            sameDay: null,
            source: null,
          },
        }}
        stateName="New Hampshire"
        {...LOOKUP}
      />
    );
    expect(
      wide().getByText("Register at your polling place on Election Day and vote.")
    ).toBeInTheDocument();
    expect(
      wide().getByRole("link", {
        name: "How to register: vote.gov, unconfirmed (opens in new tab)",
      })
    ).toHaveAttribute("href", "https://vote.gov/");
    expect(
      wide().getByText("This site blocks automated checks, so we can't confirm it's up.")
    ).toBeInTheDocument();
  });
});

describe("VoterCalendar: republished deadlines", () => {
  it("says visibly whose printing the deadlines are read from, and why", () => {
    const rep = item({
      source: {
        publisher: "Board of Elections in the City of New York",
        url: "https://www.vote.nyc/page/registration-deadlines",
        type: "official",
      },
      verification: { status: "republished", checkedOn: "2026-10-16" },
    });
    render(
      <VoterCalendar
        calendar={{
          ...calendar,
          items: [rep],
          republished:
            "These deadlines are as published by the Board of Elections in the City of New York; New York's own source blocks automated reads.",
        }}
        stateName="New York"
        {...LOOKUP}
      />
    );
    expect(wide().getByText(/^These deadlines are as published by the Board/)).toBeInTheDocument();
    expect(
      wide().getByRole("link", {
        name: /^As published by Board of Elections in the City of New York, checked/,
      })
    ).toHaveAttribute("href", "https://www.vote.nyc/page/registration-deadlines");
  });
});
