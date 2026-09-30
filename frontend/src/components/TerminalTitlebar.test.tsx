import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import TerminalTitlebar from "./TerminalTitlebar";

describe("TerminalTitlebar", () => {
  it("renders the given title, with children alongside it", () => {
    render(
      <TerminalTitlebar title="Search">
        <span data-testid="extra">extra content</span>
      </TerminalTitlebar>
    );
    expect(screen.getByText("Search")).toBeInTheDocument();
    expect(screen.getByTestId("extra")).toBeInTheDocument();
  });

  it("is decorative and hidden from assistive tech", () => {
    const { container } = render(<TerminalTitlebar title="Coverage" />);
    expect(container.querySelector('[aria-hidden="true"]')).not.toBeNull();
  });
});
