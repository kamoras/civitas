import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import GeneralRunoffNotice, {
  otherPrimaryLabel,
  RankedChoiceNotice,
  TopPrimaryNotice,
} from "./GeneralRunoffNotice";

describe("TopPrimaryNotice", () => {
  it("explains that one party can hold both November places", () => {
    render(<TopPrimaryNotice rules={[{ offices: "*", advance: 2 }]} stateName="California" />);
    expect(screen.getByText(/California held a top-two primary/)).toBeInTheDocument();
    expect(screen.getByText(/whatever their party/)).toBeInTheDocument();
  });

  it("words a top four and a scoped rule", () => {
    render(<TopPrimaryNotice rules={[{ offices: ["H"], advance: 4 }]} stateName="Alaska" />);
    expect(
      screen.getByText(/^For the U\.S\. House contests, Alaska held a top-four primary/)
    ).toBeInTheDocument();
  });

  it("says nothing without a rule", () => {
    const { container } = render(<TopPrimaryNotice rules={[]} stateName="Colorado" />);
    expect(container).toBeEmptyDOMElement();
  });
});

describe("RankedChoiceNotice", () => {
  it("tells the voter they can rank, and when the rounds are counted", () => {
    render(<RankedChoiceNotice rankedChoice stateName="Maine" />);
    expect(
      screen.getByText(/Maine decides its U\.S\. Senate and House contests by ranked-choice/)
    ).toBeInTheDocument();
    expect(screen.getByText(/ranking more than one is optional/)).toBeInTheDocument();
    expect(screen.getByText(/after election night/)).toBeInTheDocument();
  });

  it("says nothing for a plurality state or an older API", () => {
    const { container } = render(<RankedChoiceNotice stateName="Ohio" />);
    expect(container).toBeEmptyDOMElement();
  });
});

describe("GeneralRunoffNotice", () => {
  it("says an open primary is one, with its runoff date", () => {
    render(
      <GeneralRunoffNotice
        runoffs={[{ offices: ["H"], openPrimary: true, runoffDate: "2030-12-14" }]}
        electionDate="2030-11-05"
      />
    );
    expect(
      screen.getByText(/U\.S\. House contests, November 5, 2030 is an all-party primary/)
    ).toBeInTheDocument();
    expect(screen.getByText(/runoff on December 14/)).toBeInTheDocument();
  });

  it("says a majority rule covers every contest", () => {
    render(
      <GeneralRunoffNotice
        runoffs={[{ offices: "*", openPrimary: false, runoffDate: "2026-12-01" }]}
        electionDate="2026-11-03"
      />
    );
    expect(
      screen.getByText(/every contest on this ballot, winning takes a majority/)
    ).toBeInTheDocument();
    expect(screen.getByText(/December 1\./)).toBeInTheDocument();
  });

  it("says nothing for a plurality state or an older API", () => {
    const { container } = render(
      <GeneralRunoffNotice runoffs={undefined} electionDate="2026-11-03" />
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("names the contests a separate primary date is for", () => {
    expect(otherPrimaryLabel({ offices: ["H"], districts: [1, 2, 6, 7], date: "2026-08-11" })).toBe(
      "U.S. House 1, 2, 6, 7"
    );
    expect(otherPrimaryLabel({ offices: ["H"], date: "2026-11-03" })).toBe("U.S. House");
  });
});
