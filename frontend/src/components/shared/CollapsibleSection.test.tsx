import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import CollapsibleSection from "./CollapsibleSection";

describe("CollapsibleSection", () => {
  it("puts the toggle inside the heading, so the heading stays a heading", () => {
    render(<CollapsibleSection title="Holdings">body</CollapsibleSection>);
    const heading = screen.getByRole("heading", { name: "Holdings" });
    expect(heading.querySelector("button")).not.toBeNull();
    expect(screen.getByRole("button", { name: /Holdings/ }).closest("h3")).toBe(heading);
  });

  it("names the heading by its title alone, whatever the summary says", () => {
    render(
      <CollapsibleSection
        title="Holdings"
        summary="213 assets"
        source="disclosures-clerk.house.gov"
      >
        body
      </CollapsibleSection>
    );
    expect(screen.getByRole("heading", { name: "Holdings" })).toBeTruthy();
    // The whole header is still the toggle.
    expect(screen.getByRole("button", { name: /Holdings.*213 assets/ })).toBeTruthy();
  });

  it("keeps the controlled region in the DOM while collapsed, and mounts the body only when open", () => {
    render(<CollapsibleSection title="Holdings">body text</CollapsibleSection>);
    const toggle = screen.getByRole("button", { name: /Holdings/ });
    const region = document.getElementById(toggle.getAttribute("aria-controls")!);
    expect(region).not.toBeNull();
    expect(region!.hidden).toBe(true);
    expect(screen.queryByText("body text")).toBeNull();
    fireEvent.click(toggle);
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    expect(region!.hidden).toBe(false);
    expect(screen.getByText("body text")).toBeTruthy();
  });
});
