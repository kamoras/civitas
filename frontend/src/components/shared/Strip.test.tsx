import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import axe from "axe-core";
import StripPoints, { BigFigure, partySeat, StripAxis, type StripPoint } from "./Strip";

const point = (id: string, x: number, rank: string): StripPoint => ({
  id,
  name: `Member ${id}`,
  detail: `D-OR`,
  shown: `${x}%`,
  rank,
  href: `/politicians/${id}`,
  x,
  y: 20,
});

const POINTS = [
  point("c", 90, "Highest of 3"),
  point("a", 10, "Lowest of 3"),
  point("b", 50, "2nd highest of 3"),
];

function strip() {
  render(
    <main>
      <StripPoints label="Money from PACs, Senate Democrats" points={POINTS} startId="b">
        <span />
      </StripPoints>
    </main>
  );
  const group = screen.getByRole("group", { name: /Money from PACs, Senate Democrats: 3 marks/ });
  // A 200px strip: x 10/50/90 sit at 20/100/180px.
  group.getBoundingClientRect = () =>
    ({ left: 0, top: 0, width: 200, height: 40, right: 200, bottom: 40 }) as DOMRect;
  return group;
}

const label = () => document.querySelector<HTMLAnchorElement>("a[aria-hidden='true']");

describe("StripPoints", () => {
  it("is one Tab stop the arrow keys walk by value, announcing each mark", async () => {
    const group = strip();
    await userEvent.tab();
    expect(group).toHaveFocus();
    expect(label()).toBeNull();
    // The first arrow starts at the page's own mark.
    await userEvent.keyboard("{ArrowRight}");
    expect(screen.getByText(/Member b, D-OR: 50%, 2nd highest of 3\./)).toBeInTheDocument();
    expect(label()).toHaveAttribute("href", "/politicians/b");
    await userEvent.keyboard("{ArrowRight}");
    expect(label()).toHaveAttribute("href", "/politicians/c");
    await userEvent.keyboard("{ArrowRight}");
    expect(label()).toHaveAttribute("href", "/politicians/c");
    await userEvent.keyboard("{Home}");
    expect(label()).toHaveTextContent("Member a");
    expect(label()).toHaveTextContent("10% · Lowest of 3");
    await userEvent.keyboard("{End}");
    expect(label()).toHaveAttribute("href", "/politicians/c");
    const opened = vi.fn((e: MouseEvent) => e.preventDefault());
    label()!.addEventListener("click", opened);
    await userEvent.keyboard("{Enter}");
    expect(opened).toHaveBeenCalledOnce();
    await userEvent.keyboard("{Escape}");
    expect(label()).toBeNull();
    // Still one Tab stop: the label is not one.
    expect(label()).toBeNull();
    const r = await axe.run(document.body, { rules: { "color-contrast": { enabled: false } } });
    expect(r.violations.map((v) => v.id)).toEqual([]);
  });

  it("picks the nearest mark along the axis, within 24px, and a click opens it", () => {
    const group = strip();
    fireEvent.pointerMove(group, { pointerType: "mouse", clientX: 115, clientY: 5 });
    expect(label()).toHaveAttribute("href", "/politicians/b");
    fireEvent.pointerMove(group, { pointerType: "mouse", clientX: 60, clientY: 5 });
    expect(label()).toBeNull(); // 40px from the nearest mark
    fireEvent.pointerMove(group, { pointerType: "mouse", clientX: 170, clientY: 5 });
    expect(label()).toHaveAttribute("href", "/politicians/c");
    const opened = vi.fn((e: MouseEvent) => e.preventDefault());
    label()!.addEventListener("click", opened);
    fireEvent.pointerDown(group, { pointerType: "mouse" });
    fireEvent.click(group, { clientX: 170, clientY: 5 });
    expect(opened).toHaveBeenCalledOnce();
    // Moving onto the label keeps it (WCAG 1.4.13); leaving the strip closes it.
    fireEvent.pointerMove(label()!, { pointerType: "mouse", clientX: 10, clientY: -10 });
    expect(label()).toHaveAttribute("href", "/politicians/c");
    fireEvent.pointerLeave(group, { pointerType: "mouse" });
    expect(label()).toBeNull();
  });

  it("on touch, a tap shows the label and a tap elsewhere closes it", () => {
    const group = strip();
    fireEvent.pointerDown(group, { pointerType: "touch" });
    fireEvent.click(group, { clientX: 22, clientY: 20 });
    expect(label()).toHaveAttribute("href", "/politicians/a");
    // A second tap on the same mark keeps the label; its link opens the page.
    fireEvent.pointerDown(group, { pointerType: "touch" });
    fireEvent.click(group, { clientX: 22, clientY: 20 });
    expect(label()).toHaveAttribute("href", "/politicians/a");
    fireEvent.pointerDown(document.body, { pointerType: "touch" });
    expect(label()).toBeNull();
  });
});

describe("StripAxis", () => {
  it("puts the tick's label at the tick, on its own line, slid to stay inside", () => {
    render(<StripAxis start="0%" end="100%" tick={{ at: 80, text: "median 80%" }} />);
    const tick = screen.getByText("median 80%");
    expect(tick.style.left).toBe("80%");
    expect(tick.style.transform).toBe("translateX(-80%)");
    expect(tick.parentElement).not.toBe(screen.getByText("0%").parentElement);
  });
});

describe("BigFigure", () => {
  it("puts a figure's words on a smaller line under the number", () => {
    render(<BigFigure shown="0.67 million a year" className="big" />);
    expect(screen.getByText("million a year")).toHaveClass("block");
    expect(screen.getByText("million a year").parentElement).toHaveTextContent(
      "0.67 million a year"
    );
  });

  it("leaves a bare figure, and says when there is none", () => {
    const { rerender } = render(<BigFigure shown="13%" className="big" />);
    expect(screen.getByText("13%")).toBeInTheDocument();
    rerender(<BigFigure shown={null} className="big" />);
    expect(screen.getByText("No record")).toBeInTheDocument();
  });
});

describe("partySeat", () => {
  it("prints party and seat", () => {
    expect(partySeat({ party: "D", state: "CA", district: null }, false)).toBe("D-CA");
    expect(partySeat({ party: "R", state: "TX", district: 5 }, true)).toBe("R-TX-5");
    expect(partySeat({ party: "R", state: "WY", district: 0 }, true)).toBe("R-WY-AL");
  });
});
