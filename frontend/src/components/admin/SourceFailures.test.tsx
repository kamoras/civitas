import { describe, expect, it } from "vitest";
import { render, screen, within } from "@testing-library/react";
import axe from "axe-core";
import { SourceFailuresView } from "./SourceFailures";

const data = {
  days: 30,
  since: "2026-09-27",
  total: 5,
  hosts: [
    {
      host: "efdsearch.senate.gov",
      total: 4,
      byKind: { walled: 4, refused: 0, unavailable: 0, untrusted: 0 },
      steps: ["stock trades · scan_pages", "stock trades · senate_ptr"],
      lastDate: "2026-10-25",
      barPct: 100,
      mark: {
        id: "efdsearch.senate.gov",
        label: "efdsearch.senate.gov",
        shown: "4 failed steps",
        detail: "stock trades · scan_pages, stock trades · senate_ptr",
        context: "walled 4; last 2026-10-25",
      },
    },
    {
      host: "api.open.fec.gov",
      total: 1,
      byKind: { walled: 0, refused: 0, unavailable: 1, untrusted: 0 },
      steps: ["senate · fetch"],
      lastDate: "2026-10-02",
      barPct: 25,
      mark: {
        id: "api.open.fec.gov",
        label: "api.open.fec.gov",
        shown: "1 failed step",
        context: "unavailable 1; last 2026-10-02",
      },
    },
  ],
};

describe("SourceFailuresView", () => {
  it("draws a bar per host as the API sizes it, with a table as its text", async () => {
    const { container } = render(
      <main>
        <SourceFailuresView data={data} />
      </main>
    );
    const bars = container.querySelectorAll<HTMLElement>("[data-mark]");
    expect([...bars].map((b) => b.style.width)).toEqual(["100%", "25%"]);
    expect(screen.getByRole("group", { name: /Expected source failures by host/ })).toBeTruthy();
    const rows = within(screen.getByRole("table")).getAllByRole("row");
    expect(rows[1]).toHaveTextContent("efdsearch.senate.gov");
    expect(rows[1]).toHaveTextContent("walled 4");
    expect(rows[2]).toHaveTextContent("unavailable 1");
    const result = await axe.run(document.body, {
      rules: { "color-contrast": { enabled: false } },
    });
    expect(result.violations.map((v) => v.id)).toEqual([]);
  });

  it("says when no source failed a step", () => {
    render(<SourceFailuresView data={{ ...data, hosts: [], total: 0 }} />);
    expect(
      screen.getByText("No source refused or failed a step in the last 30 days.")
    ).toBeInTheDocument();
  });
});
