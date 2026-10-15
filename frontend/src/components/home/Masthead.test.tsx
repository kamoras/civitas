import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import Masthead from "./Masthead";

const push = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));

afterEach(() => {
  cleanup();
  push.mockReset();
});

function request(text: string) {
  render(<Masthead />);
  const box = screen.getByLabelText("TOPIC · BILL NO. · DOCUMENT");
  fireEvent.change(box, { target: { value: text } });
  fireEvent.submit(box.closest("form")!);
}

describe("Masthead record request", () => {
  it("opens a bill's page for a bill number, however it is spelled", () => {
    // Full-text search ranked unrelated documents sharing the digits first.
    request("H.R. 7008");
    expect(push).toHaveBeenCalledWith("/congress/bills/HR.7008");
  });

  it("searches anything else", () => {
    request("drug prices");
    expect(push).toHaveBeenCalledWith("/explore?q=drug%20prices");
  });
});

describe("Masthead voter links", () => {
  it("go to the pages that ask for a state until one is picked, then to that state's", () => {
    render(<Masthead />);
    const voters = screen.getByRole("navigation", { name: "For voters" });
    const href = (name: string) =>
      [...voters.querySelectorAll("a")].find((a) => a.textContent === name)!.getAttribute("href");
    expect(href("YOUR MEMBERS")).toBe("/politicians");
    expect(href("YOUR BALLOT")).toBe("/elections");
    fireEvent.change(screen.getByLabelText("YOUR STATE:"), { target: { value: "OH" } });
    expect(href("YOUR MEMBERS")).toBe("/politicians?state=OH");
    expect(href("YOUR BALLOT")).toBe("/elections/states/OH");
    expect(href("CONGRESS THIS WEEK")).toBe("/congress");
    // A navigation choice only: nothing is pushed or stored.
    expect(push).not.toHaveBeenCalled();
  });
});
