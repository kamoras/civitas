import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import CoverageNote from "./CoverageNote";
import { fetchCoverage } from "@/lib/congressServer";

vi.mock("@/lib/congressServer", () => ({ fetchCoverage: vi.fn() }));

const coverage = {
  firstDay: "2087-01-03",
  firstCongress: 150,
  rollCallCongresses: [150],
  sentence: "Civitas's record of Congress starts January 3, 2087, in the 150th Congress.",
  rollCallSentence: "Civitas holds roll calls from the 150th Congress on.",
  congressionalRecordUrl: "https://www.govinfo.gov/app/collection/crec",
  rollCallSources: {
    senate: "https://www.senate.gov/legislative/votes_new.htm",
    house: "https://clerk.house.gov/Votes",
  },
};

describe("a /congress 404 states where the record starts", () => {
  it("for a day, week or month, with the Congressional Record linked", async () => {
    vi.mocked(fetchCoverage).mockResolvedValue(coverage);
    render(await CoverageNote({ of: "days" }));
    expect(screen.getByText(/starts January 3, 2087/)).toBeTruthy();
    expect(
      screen.getByRole("link", { name: "the Congressional Record at GovInfo" })
    ).toHaveAttribute("href", coverage.congressionalRecordUrl);
  });

  it("for a roll call, with each chamber's votes linked", async () => {
    vi.mocked(fetchCoverage).mockResolvedValue(coverage);
    render(await CoverageNote({ of: "rollCalls" }));
    expect(screen.getByText(/from the 150th Congress on/)).toBeTruthy();
    expect(screen.getByRole("link", { name: "the House Clerk" })).toHaveAttribute(
      "href",
      "https://clerk.house.gov/Votes"
    );
  });

  it("says nothing when the backend can't answer", async () => {
    vi.mocked(fetchCoverage).mockResolvedValue(null);
    expect(await CoverageNote({ of: "days" })).toBeNull();
  });
});
