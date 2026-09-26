import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import BallotBasisNotice from "./BallotBasisNotice";
import type { BallotBasis } from "@/types/election";

function basis(over: Partial<BallotBasis> = {}): BallotBasis {
  return {
    basis: "confirmed",
    primaryPassed: true,
    daysSincePrimary: 130,
    supersededByPrimary: false,
    ...over,
  };
}

describe("BallotBasisNotice", () => {
  it("says plainly that filers after a primary are not ballot positions", () => {
    render(
      <BallotBasisNotice
        basis={basis({ basis: "filers", supersededByPrimary: true, daysSincePrimary: 95 })}
      />,
    );
    expect(screen.getByTestId("ballot-superseded")).toBeInTheDocument();
    expect(screen.getByText(/NOT BALLOT POSITIONS/i)).toBeInTheDocument();
    expect(screen.getByText(/95 days ago/)).toBeInTheDocument();
  });

  it("omits the day count rather than inventing one", () => {
    render(
      <BallotBasisNotice
        basis={basis({ basis: "filers", supersededByPrimary: true, daysSincePrimary: null })}
      />,
    );
    expect(screen.getByTestId("ballot-superseded")).toBeInTheDocument();
    expect(screen.queryByText(/days ago/)).not.toBeInTheDocument();
  });

  it("treats pre-primary filers as an honest answer, not an error", () => {
    render(
      <BallotBasisNotice
        basis={basis({ basis: "filers", primaryPassed: false, supersededByPrimary: false })}
      />,
    );
    expect(screen.queryByTestId("ballot-superseded")).not.toBeInTheDocument();
    expect(screen.getByTestId("ballot-filers")).toBeInTheDocument();
  });

  it("says nothing when the API has not sent the field yet", () => {
    // Frontend and backend deploy as separate containers; a frontend
    // that lands first must degrade to the old page, not a blank one.
    const { container } = render(<BallotBasisNotice basis={undefined} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("says nothing at all when the ballot is certified", () => {
    // A notice on every page is one readers learn to skip.
    const { container } = render(<BallotBasisNotice basis={basis({ basis: "confirmed" })} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("discloses that primary-derived nominees can be incomplete", () => {
    render(<BallotBasisNotice basis={basis({ basis: "nominees" })} />);
    expect(screen.getByTestId("ballot-nominees")).toBeInTheDocument();
    expect(screen.getByText(/may be incomplete/i)).toBeInTheDocument();
  });

  it("warns that a primary ballot is not a general-election ballot", () => {
    render(<BallotBasisNotice basis={basis({ basis: "primary" })} />);
    expect(screen.getByTestId("ballot-primary")).toBeInTheDocument();
  });
});
