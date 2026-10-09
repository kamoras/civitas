import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import GeneralRunoffNotice, { otherPrimaryLabel } from "./GeneralRunoffNotice";

describe("GeneralRunoffNotice", () => {
  it("says an open primary is one, with its runoff date", () => {
    render(
      <GeneralRunoffNotice
        runoffs={[{ offices: ["H"], openPrimary: true, runoffDate: "2026-12-12" }]}
      />
    );
    expect(
      screen.getByText(/U\.S\. House contests, November 3 is an all-party primary/)
    ).toBeInTheDocument();
    expect(screen.getByText(/runoff on December 12/)).toBeInTheDocument();
  });

  it("says a majority rule covers every contest", () => {
    render(
      <GeneralRunoffNotice
        runoffs={[{ offices: "*", openPrimary: false, runoffDate: "2026-12-01" }]}
      />
    );
    expect(
      screen.getByText(/every contest on this ballot, winning takes a majority/)
    ).toBeInTheDocument();
    expect(screen.getByText(/December 1\./)).toBeInTheDocument();
  });

  it("says nothing for a plurality state or an older API", () => {
    const { container } = render(<GeneralRunoffNotice runoffs={undefined} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("names the contests a separate primary date is for", () => {
    expect(otherPrimaryLabel({ offices: ["H"], districts: [1, 2, 6, 7], date: "2026-08-11" })).toBe(
      "U.S. HOUSE 1, 2, 6, 7"
    );
    expect(otherPrimaryLabel({ offices: ["H"], date: "2026-11-03" })).toBe("U.S. HOUSE");
  });
});
