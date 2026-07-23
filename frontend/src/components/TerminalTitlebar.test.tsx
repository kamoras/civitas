import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import TerminalTitlebar from "./TerminalTitlebar";

describe("TerminalTitlebar", () => {
  it("renders the given title", () => {
    render(<TerminalTitlebar title="ai_analysis.sh" />);
    expect(screen.getByText("ai_analysis.sh")).toBeInTheDocument();
  });

  it("renders children alongside the title", () => {
    render(
      <TerminalTitlebar title="explore.sh">
        <span data-testid="extra">extra content</span>
      </TerminalTitlebar>,
    );
    expect(screen.getByTestId("extra")).toBeInTheDocument();
  });

  it("is decorative and hidden from assistive tech", () => {
    const { container } = render(<TerminalTitlebar title="x.sh" />);
    expect(container.querySelector('[aria-hidden="true"]')).not.toBeNull();
  });
});
