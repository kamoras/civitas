import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import axe from "axe-core";
import DecisionLineups from "./DecisionLineups";
import { court } from "./court.fixture";

const mark = (term: number, split: string, label: string) => ({
  id: `${term}-${split}`,
  label,
  detail: `${term} term`,
  shown: "1 case",
  context: "50% of the term's 2",
});

describe("DecisionLineups", () => {
  it("reads each segment in the API's words, sized by the API's share", async () => {
    const c = court({
      lineups: {
        splits: ["3-0", "2-1"],
        terms: [2024, 2025].map((term) => ({
          term,
          cases: 2,
          counts: { "3-0": 1, "2-1": 1 },
          shares: { "3-0": 50, "2-1": 50 },
          marks: [mark(term, "3-0", "3–0"), mark(term, "2-1", "2–1")],
        })),
      },
    });
    const { container } = render(
      <main>
        <DecisionLineups court={c} />
      </main>
    );
    expect(container.querySelector<HTMLElement>("[data-mark='2025-2-1']")!.style.width).toBe("50%");
    const chart = screen.getByRole("group", { name: /^How the Court split, by term: 4 marks/ });
    chart.focus();
    await userEvent.keyboard("{ArrowRight}");
    expect(screen.getByText("3–0, 2025 term: 1 case, 50% of the term's 2.")).toBeInTheDocument();
    const r = await axe.run(document.body, { rules: { "color-contrast": { enabled: false } } });
    expect(r.violations.map((v) => v.id)).toEqual([]);
  });

  it("draws plain from an older API, sized by the counts", () => {
    const { container } = render(<DecisionLineups court={court()} />);
    expect(screen.queryByRole("group")).toBeNull();
    expect(container.querySelector<HTMLElement>("[data-mark='2024-3-0']")!.style.width).toBe("50%");
    expect(screen.getByRole("table")).toBeInTheDocument();
  });
});
