import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import TerminalTitlebar from "./TerminalTitlebar";

describe("TerminalTitlebar", () => {
  it("renders the given title", () => {
    render(<TerminalTitlebar title="Analysis" />);
    expect(screen.getByText("Analysis")).toBeInTheDocument();
  });

  it("renders children alongside the title", () => {
    render(
      <TerminalTitlebar title="Search">
        <span data-testid="extra">extra content</span>
      </TerminalTitlebar>
    );
    expect(screen.getByTestId("extra")).toBeInTheDocument();
  });

  it("is decorative and hidden from assistive tech", () => {
    const { container } = render(<TerminalTitlebar title="Coverage" />);
    expect(container.querySelector('[aria-hidden="true"]')).not.toBeNull();
  });
});
