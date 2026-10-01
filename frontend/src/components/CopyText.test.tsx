import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import CopyText from "./CopyText";

const URL = "https://civitas-research.org/feed/elections.xml";

/** userEvent.setup() installs its own clipboard stub, so ours goes in after. */
function stubClipboard(writeText: () => Promise<void>) {
  Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
}

describe("CopyText", () => {
  it("copies the address and announces it in a live region, not by renaming the button", async () => {
    const user = userEvent.setup();
    const writeText = vi.fn().mockResolvedValue(undefined);
    stubClipboard(writeText);
    render(<CopyText text={URL} label="Elections feed address" />);

    await user.click(screen.getByRole("button", { name: "Copy the Elections feed address" }));

    expect(writeText).toHaveBeenCalledWith(URL);
    await waitFor(() =>
      expect(screen.getByRole("status")).toHaveTextContent("Elections feed address copied.")
    );
    // The button keeps its name, so focus doesn't land on a renamed control.
    expect(
      screen.getByRole("button", { name: "Copy the Elections feed address" })
    ).toHaveTextContent("[ COPIED! ]");
    expect(screen.getByRole("status")).toHaveClass("sr-only");
  });

  it("says so, visibly, when the browser refuses", async () => {
    const user = userEvent.setup();
    stubClipboard(vi.fn().mockRejectedValue(new Error("denied")));
    render(<CopyText text={URL} label="Elections feed address" />);

    await user.click(screen.getByRole("button", { name: "Copy the Elections feed address" }));

    await waitFor(() =>
      expect(screen.getByRole("status")).toHaveTextContent("This browser wouldn't copy it.")
    );
    expect(screen.getByRole("status")).not.toHaveClass("sr-only");
    // The address itself stays on screen to select by hand.
    expect(screen.getByText(URL)).toBeInTheDocument();
  });
});
