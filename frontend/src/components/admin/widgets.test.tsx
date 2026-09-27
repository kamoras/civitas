import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { UsageBar } from "./widgets";

describe("UsageBar", () => {
  it("announces the displayed value, not the rounded or scaled percentage", () => {
    render(
      <>
        <UsageBar pct={0.4} ariaLabel="CPU utilisation" valueText="0.4%" />
        <UsageBar pct={(57 / 85) * 100} ariaLabel="CPU temperature" valueText="57°C" />
      </>
    );
    expect(screen.getByRole("progressbar", { name: "CPU utilisation" })).toHaveAttribute(
      "aria-valuetext",
      "0.4%"
    );
    expect(screen.getByRole("progressbar", { name: "CPU temperature" })).toHaveAttribute(
      "aria-valuetext",
      "57°C"
    );
  });
});
