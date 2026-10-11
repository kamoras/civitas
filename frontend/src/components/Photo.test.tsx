import { describe, expect, it } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import Photo from "./Photo";

describe("Photo", () => {
  it("shows the fallback, not a broken image, when the photo route has none", () => {
    render(<Photo src="/photo/bioguide/X000001" alt="A member" fallback={<span>AM</span>} />);
    fireEvent.error(screen.getByRole("img", { name: "A member" }));
    expect(screen.queryByRole("img")).toBeNull();
    expect(screen.getByText("AM")).toBeTruthy();
  });

  it("lazy-loads only when asked", () => {
    render(<Photo src="/photo/bioguide/X000001" alt="A" lazy />);
    expect(screen.getByRole("img").getAttribute("loading")).toBe("lazy");
  });
});
