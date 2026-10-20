import { afterEach, describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ThemeToggle, { THEME_KEY } from "./ThemeToggle";

afterEach(() => {
  delete document.documentElement.dataset.theme;
  window.sessionStorage.clear();
});

describe("ThemeToggle", () => {
  it("switches this tab to dark and back, kept in sessionStorage only", async () => {
    render(<ThemeToggle />);
    await userEvent.click(screen.getByRole("button", { name: "Dark theme" }));
    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(window.sessionStorage.getItem(THEME_KEY)).toBe("dark");
    expect(window.localStorage.getItem(THEME_KEY)).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Light theme" }));
    expect(document.documentElement.dataset.theme).toBe("light");
  });
});
