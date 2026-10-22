import { describe, expect, it } from "vitest";
import { olderApi } from "@/lib/olderApi";
import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import axe from "axe-core";
import { MARK_ATTR } from "@/types/readout";
import ChartReadout, { type ChartMark } from "./ChartReadout";
import RecordFigure from "./RecordFigure";

// A 2x2 grid of 40px cells, the second row linked.
const MARKS: ChartMark[] = [
  { id: "a", label: "Alpha", shown: "1 vote", context: "25% of the 4 voting" },
  { id: "b", label: "Beta", shown: "1 vote" },
  { id: "c", label: "Gamma", shown: "2 votes", href: "/c" },
  { id: "d", label: "Delta", shown: "0 votes", href: "/d" },
];
const CELL: Record<string, DOMRect> = Object.fromEntries(
  MARKS.map((m, i) => {
    const left = (i % 2) * 40;
    const top = Math.floor(i / 2) * 40;
    return [
      m.id,
      { left, top, width: 40, height: 40, right: left + 40, bottom: top + 40 } as DOMRect,
    ];
  })
);

function grid() {
  render(
    <main>
      <ChartReadout label="A grid" marks={MARKS} cols={2} labelAt="mark">
        {MARKS.map((m) => (
          <span key={m.id} {...{ [MARK_ATTR]: m.id }} />
        ))}
      </ChartReadout>
    </main>
  );
  const group = screen.getByRole("group", { name: /^A grid: 4 marks/ });
  group.getBoundingClientRect = () =>
    ({ left: 0, top: 0, width: 80, height: 80, right: 80, bottom: 80 }) as DOMRect;
  group.querySelectorAll<HTMLElement>(`[${MARK_ATTR}]`).forEach((el) => {
    el.getBoundingClientRect = () => CELL[el.getAttribute(MARK_ATTR)!];
  });
  return group;
}

const readout = () => document.querySelector("[aria-hidden='true'].z-20");

describe("ChartReadout", () => {
  it("rings the copy of a mark under the pointer when it is drawn twice", () => {
    // One mark drawn in two charts (an election's electoral bar above and
    // its popular-vote column below): the lower copy is the larger.
    const marks: ChartMark[] = [{ id: "y", label: "1960", shown: "56%" }];
    const rect = (top: number, height: number) =>
      ({ left: 0, top, width: 40, height, right: 40, bottom: top + height }) as DOMRect;
    render(
      <main>
        <ChartReadout label="Two rows" marks={marks}>
          <span data-row="top" {...{ [MARK_ATTR]: "y" }} />
          <span data-row="bottom" {...{ [MARK_ATTR]: "y" }} />
        </ChartReadout>
      </main>
    );
    const group = screen.getByRole("group", { name: /^Two rows/ });
    group.getBoundingClientRect = () => rect(0, 200);
    group.querySelector<HTMLElement>("[data-row=top]")!.getBoundingClientRect = () => rect(0, 20);
    group.querySelector<HTMLElement>("[data-row=bottom]")!.getBoundingClientRect = () =>
      rect(100, 100);
    const ring = () => document.querySelector<HTMLElement>(".outline-offset-1");
    fireEvent.pointerMove(group, { pointerType: "mouse", clientX: 10, clientY: 10 });
    expect(ring()?.style.top).toBe("0px");
    fireEvent.pointerMove(group, { pointerType: "mouse", clientX: 10, clientY: 150 });
    expect(ring()?.style.top).toBe("100px");
  });

  it("picks a shape under the pointer, or the nearest within 24px", () => {
    const group = grid();
    fireEvent.pointerMove(group, { pointerType: "mouse", clientX: 50, clientY: 10 });
    expect(readout()).toHaveTextContent("Beta");
    // 20px right of the grid: still the nearest cell.
    fireEvent.pointerMove(group, { pointerType: "mouse", clientX: 100, clientY: 60 });
    expect(readout()).toHaveTextContent("Delta");
    expect(readout()).toHaveAttribute("href", "/d");
    // 30px off: nothing.
    fireEvent.pointerMove(group, { pointerType: "mouse", clientX: 110, clientY: 60 });
    expect(readout()).toBeNull();
  });

  it("walks a grid by row with Up and Down, and a mark with no page is plain text", async () => {
    const group = grid();
    await userEvent.tab();
    expect(group).toHaveFocus();
    await userEvent.keyboard("{ArrowRight}");
    expect(readout()).toHaveTextContent("Alpha1 vote · 25% of the 4 voting");
    expect(readout()?.tagName).toBe("DIV");
    expect(screen.getByText("Alpha: 1 vote, 25% of the 4 voting.")).toBeInTheDocument();
    await userEvent.keyboard("{ArrowDown}");
    expect(readout()).toHaveTextContent("Gamma");
    await userEvent.keyboard("{ArrowRight}");
    expect(readout()).toHaveTextContent("Delta");
    await userEvent.keyboard("{ArrowUp}");
    expect(readout()).toHaveTextContent("Beta");
    await userEvent.keyboard("{Escape}");
    expect(readout()).toBeNull();
    const r = await axe.run(document.body, { rules: { "color-contrast": { enabled: false } } });
    expect(r.violations.map((v) => v.id)).toEqual([]);
  });
});

describe("RecordFigure readouts", () => {
  it("names each dot, starting at the breaker", async () => {
    render(
      <RecordFigure
        size="box"
        f={{
          type: "dots",
          party: "R",
          voting: 2,
          caption: "1 of 2 Republicans voted Yea",
          marks: [
            {
              id: "x",
              label: "Xi Roe",
              detail: "R-TX",
              shown: "Voted Nay",
              href: "/politicians/x",
            },
            {
              id: "y",
              label: "Yu Doe",
              detail: "R-AA",
              shown: "Voted Yea",
              context: "The only Republican to vote Yea",
              href: "/politicians/y",
            },
          ],
        }}
      />
    );
    expect(document.querySelectorAll(`circle[${MARK_ATTR}]`)).toHaveLength(2);
    await userEvent.tab();
    await userEvent.keyboard("{ArrowRight}");
    expect(
      screen.getByText("Yu Doe, R-AA: Voted Yea, The only Republican to vote Yea.")
    ).toBeInTheDocument();
    expect(readout()).toHaveAttribute("href", "/politicians/y");
    await userEvent.keyboard("{Home}");
    expect(readout()).toHaveAttribute("href", "/politicians/x");
  });
});

describe("an older API, during a rolling update", () => {
  it("draws a chart plain when its marks are missing", () => {
    const { container } = render(
      <ChartReadout label="A grid" marks={undefined}>
        <span>drawing</span>
      </ChartReadout>
    );
    expect(screen.queryByRole("group")).toBeNull();
    expect(container.querySelector("[tabindex]")).toBeNull();
    expect(container.textContent).toBe("drawing");
  });

  it("draws every record figure plain without its marks", () => {
    const figures = [
      { type: "dots", party: "R", voting: 3, caption: "1 of 3 Republicans voted Yea" },
      { type: "split", yeas: 51, nays: 49, yeaShare: 0.51, caption: "Passed" },
      {
        type: "countdown",
        opened: "2026-10-01",
        closes: "2026-10-31",
        totalDays: 30,
        daysLeft: 10,
        elapsedPercent: 67,
      },
    ] as const;
    for (const f of figures) {
      const { container, unmount } = render(<RecordFigure size="box" f={olderApi(f) as never} />);
      expect(screen.queryByRole("group")).toBeNull();
      // Plain: drawn, with no readout, nothing printed from a missing field.
      expect(container.textContent).not.toMatch(/undefined|NaN/);
      unmount();
    }
    const { container } = render(<RecordFigure size="box" f={olderApi(figures[0]) as never} />);
    expect(container.querySelectorAll("circle")).toHaveLength(3);
  });
});
