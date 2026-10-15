import { describe, expect, it } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import Photo, { PHOTO_SRCSET_WIDTHS } from "./Photo";
import { PHOTO_WIDTHS } from "@/lib/photos";

describe("Photo", () => {
  it("shows the fallback, not a broken image, when the photo route has none", () => {
    render(
      <Photo src="/photo/bioguide/X000001" alt="A member" sizes="48px" fallback={<span>AM</span>} />
    );
    fireEvent.error(screen.getByRole("img", { name: "A member" }));
    expect(screen.queryByRole("img")).toBeNull();
    expect(screen.getByText("AM")).toBeTruthy();
  });

  it("lazy-loads only when asked", () => {
    render(<Photo src="/photo/bioguide/X000001" alt="A" sizes="48px" lazy />);
    expect(screen.getByRole("img").getAttribute("loading")).toBe("lazy");
  });

  it("offers each served width, for the slot's size, and leads with the LCP photo", () => {
    render(
      <Photo
        src="/photo/issue/795"
        alt="An issue"
        sizes="(min-width: 1024px) 832px, 100vw"
        priority
      />
    );
    const img = screen.getByRole("img");
    expect(img.getAttribute("srcset")).toBe(
      "/photo/issue/795/96 96w, /photo/issue/795/192 192w, /photo/issue/795/400 400w, " +
        "/photo/issue/795/800 800w, /photo/issue/795/1200 1200w"
    );
    expect(img.getAttribute("sizes")).toBe("(min-width: 1024px) 832px, 100vw");
    expect(img.getAttribute("fetchpriority")).toBe("high");
  });

  it("asks only for widths the route serves", () => {
    expect([...PHOTO_SRCSET_WIDTHS]).toEqual([...PHOTO_WIDTHS]);
  });
});
