import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import axe from "axe-core";
import VoteSteps, { STEP_LABEL } from "./VoteSteps";
import type { VoteStep } from "@/types/election";

function step(over: Partial<VoteStep>): VoteStep {
  return {
    task: "polling_place",
    url: "https://www.pavoterservices.pa.gov/poll",
    host: "pavoterservices.pa.gov",
    sourceName: "Pennsylvania Department of State",
    isStateSpecific: true,
    unconfirmed: false,
    fallback: null,
    deadline: null,
    ...over,
  };
}

const STEPS: VoteStep[] = [
  step({
    task: "check_registration",
    url: "https://www.pa.gov/check",
    host: "pa.gov",
    unconfirmed: true,
    fallback: {
      url: "https://www.nass.org/can-i-vote/voter-registration-status",
      sourceName: "Can I Vote (National Association of Secretaries of State)",
      host: "nass.org",
    },
  }),
  step({
    task: "register",
    url: "https://www.pa.gov/register",
    host: "pa.gov",
    deadline: {
      sentence: "Register online by Mon, Oct 19",
      status: "open",
      countdown: "tomorrow",
      closesOn: "2026-10-19",
    },
  }),
  step({
    task: "sample_ballot",
    url: "https://www.usa.gov/election-office",
    host: "usa.gov",
    sourceName: "USAGov's directory of election offices",
    isStateSpecific: false,
  }),
  step({}),
  step({
    task: "mail_ballot",
    deadline: {
      sentence: "Mail ballot requests closed Oct 27",
      status: "closed",
      countdown: null,
      closesOn: "2026-10-27",
    },
  }),
];

describe("VoteSteps", () => {
  it("one distinct button per task, in task order, each naming where it goes", async () => {
    render(
      <main>
        <VoteSteps steps={STEPS} stateName="Pennsylvania" />
      </main>
    );
    const links = screen.getAllByRole("link", { name: /opens in new tab/ });
    const buttons = links.filter((a) => !a.closest("p"));
    expect(buttons).toHaveLength(5);
    expect(new Set(Object.values(STEP_LABEL)).size).toBe(5);
    expect(buttons[1]).toHaveAccessibleName(
      "Register to vote: pa.gov · Pennsylvania Department of State. Register online by Mon, Oct 19 · 1 day left (opens in new tab)"
    );
    expect(buttons[2]).toHaveTextContent("your county election office, through USAGov");
    expect(buttons[4]).toHaveTextContent("Mail ballot requests closed Oct 27");
    // A walled state page: said, with the national fallback beside it.
    expect(
      screen.getByRole("link", { name: /Check your registration through Can I Vote/ })
    ).toHaveAttribute("href", "https://www.nass.org/can-i-vote/voter-registration-status");
    const result = await axe.run(document.body, {
      rules: { "color-contrast": { enabled: false } },
    });
    expect(result.violations).toEqual([]);
  });

  it("a state with no registration says so without a link", () => {
    render(
      <VoteSteps
        steps={[
          step({
            task: "register",
            url: null,
            host: null,
            sourceName: null,
            deadline: {
              sentence: "North Dakota does not have voter registration.",
              status: "none",
              countdown: null,
              closesOn: null,
            },
          }),
        ]}
        stateName="North Dakota"
      />
    );
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
    expect(screen.getByText("North Dakota does not have voter registration.")).toBeInTheDocument();
  });
});
