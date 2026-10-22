import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import axe from "axe-core";
import CountHistogram, { histLayout, wholeCounts, type CountPoint } from "./CountHistogram";

const pt = (id: string, value: number): CountPoint => ({
  id,
  name: `Member ${id}`,
  detail: "D-OR",
  shown: `${value} of 40`,
  rank: `rank of ${id}`,
  href: `/politicians/${id}`,
  value,
});

// The API's order: ascending by value.
const POINTS = [pt("a", 0), pt("b", 3), pt("c", 3), pt("d", 3), pt("e", 6)];

describe("wholeCounts", () => {
  it("is a count measure whose every figure is a whole number", () => {
    expect(wholeCounts("count", [0, 3, 6])).toBe(true);
    expect(wholeCounts("share", [0, 1])).toBe(false);
    expect(wholeCounts("count", [0, 2.5])).toBe(false);
    expect(wholeCounts("count", [])).toBe(false);
  });

  it("keeps the strip for a response without a unit (an older backend)", () => {
    expect(wholeCounts(undefined, [0, 3, 6])).toBe(false);
  });

  it("keeps the strip when the columns would be narrower than a dot", () => {
    expect(wholeCounts("count", [0, 49])).toBe(true);
    expect(wholeCounts("count", [0, 50])).toBe(false);
  });
});

describe("histLayout", () => {
  it("puts one column per whole number and stacks ties upward", () => {
    const L = histLayout(POINTS, 700, 16);
    expect([L.lo, L.hi]).toEqual([0, 6]);
    expect(L.colW).toBe(100);
    const at = (id: string) => L.dots.find((d) => d.p.id === id)!;
    // Each column centred on its whole number.
    expect(at("a").cx).toBe(50);
    expect(at("e").cx).toBe(650);
    // Ties in single file when they fit: one above the other.
    expect([at("b").cx, at("c").cx, at("d").cx]).toEqual([350, 350, 350]);
    expect(at("b").cy).toBeGreaterThan(at("c").cy);
    // Every dot sits above the baseline.
    for (const d of L.dots) expect(d.cy).toBeLessThan(L.baseline);
  });

  it("stacks upward in a narrow column, the highlighted at the foot", () => {
    const L = histLayout(POINTS, 70, 16, ["d"]);
    const col = L.dots.filter((d) => d.p.value === 3);
    expect(col.map((d) => d.p.id)).toEqual(["d", "b", "c"]);
    expect(new Set(col.map((d) => d.cx)).size).toBe(1);
    // Bottom to top: each next dot higher (a smaller y).
    expect(col[0].cy).toBeGreaterThan(col[1].cy);
    expect(col[1].cy).toBeGreaterThan(col[2].cy);
  });

  it("walks every person by value, up each column, then on", () => {
    const L = histLayout(POINTS, 70, 16, ["d"]);
    expect(L.order.map((d) => d.p.id)).toEqual(["a", "d", "b", "c", "e"]);
  });

  it("caps a tall column and says how many more it holds", () => {
    const many = [
      ...Array.from({ length: 105 }, (_, i) => pt(`z${i}`, 0)),
      ...Array.from({ length: 95 }, (_, i) => pt(`o${i}`, 1)),
      pt("me", 0),
      pt("top", 17),
    ];
    const L = histLayout(many, 254, 16, ["me"]);
    // Under the cap, with every person still reachable.
    expect(L.baseline - L.labelRows * L.rowH).toBeLessThanOrEqual(202);
    expect(L.order).toHaveLength(many.length);
    const zero = L.overflow.find((o) => o.value === 0)!;
    const drawn = L.dots.filter((d) => d.p.value === 0).length;
    expect(zero.n + drawn).toBe(106);
    // The highlighted member is drawn, never capped away.
    expect(L.dots.some((d) => d.p.id === "me")).toBe(true);
    // The hidden are read at their column's label.
    const hidden = L.order.filter((d) => !L.dots.includes(d) && d.p.value === 0);
    expect(hidden).toHaveLength(zero.n);
    for (const h of hidden) expect([h.cx, h.cy]).toEqual([zero.cx, zero.cy]);
    // Several abreast, with a dot's width left between columns.
    const xs = (v: number) => L.dots.filter((d) => d.p.value === v).map((d) => d.cx);
    const pitch = L.d + 2;
    expect(Math.min(...xs(1)) - Math.max(...xs(0))).toBeGreaterThanOrEqual(2 * pitch - 0.01);
    // Two capped columns side by side: their labels never overlap.
    const one = L.overflow.find((o) => o.value === 1)!;
    expect(zero.cy === one.cy ? Math.abs(zero.cx - one.cx) >= 30 : true).toBe(true);
  });

  it("thins the axis labels so they never crowd, larger text thinning more", () => {
    const wide = Array.from({ length: 41 }, (_, i) => pt(`m${i}`, i));
    const gaps = (rem: number) => {
      const t = histLayout(wide, 254, rem).ticks;
      return { n: t.length, step: t[1].value - t[0].value };
    };
    expect(gaps(16).step).toBeGreaterThan(1);
    expect(gaps(32).step).toBeGreaterThan(gaps(16).step);
    expect(histLayout(POINTS, 700, 16).ticks.map((t) => t.value)).toEqual([0, 1, 2, 3, 4, 5, 6]);
  });
});

function chart() {
  render(
    <main>
      <CountHistogram
        label="Bills past committee, Senate Democrats"
        points={POINTS}
        marked={{ d: { fill: "var(--ink-hi)", ring: "var(--phos)" } }}
        startId="d"
        tick={{ value: 3, text: "median 3" }}
      />
    </main>
  );
  return screen.getByRole("group", { name: /Bills past committee, Senate Democrats: 5 marks/ });
}

const label = () => document.querySelector<HTMLAnchorElement>("a[aria-hidden='true']");

describe("CountHistogram", () => {
  it("draws a dot a person, the highlighted ringed, the median labelled", () => {
    chart();
    const circles = document.querySelectorAll("svg circle");
    expect(circles).toHaveLength(5);
    expect([...circles].filter((c) => c.getAttribute("stroke") === "var(--phos)")).toHaveLength(1);
    expect(screen.getByText("median 3")).toBeInTheDocument();
  });

  it("is one Tab stop the arrow keys walk in value order, announcing each", async () => {
    const group = chart();
    await userEvent.tab();
    expect(group).toHaveFocus();
    await userEvent.keyboard("{ArrowRight}");
    expect(screen.getByText(/Member d, D-OR: 3 of 40, rank of d\./)).toBeInTheDocument();
    expect(label()).toHaveAttribute("href", "/politicians/d");
    // Up the column, then on to the next.
    await userEvent.keyboard("{ArrowUp}");
    expect(label()).toHaveAttribute("href", "/politicians/b");
    await userEvent.keyboard("{ArrowRight}{ArrowRight}");
    expect(label()).toHaveAttribute("href", "/politicians/e");
    await userEvent.keyboard("{Home}");
    expect(label()).toHaveAttribute("href", "/politicians/a");
    await userEvent.keyboard("{Escape}");
    expect(label()).toBeNull();
    const r = await axe.run(document.body, { rules: { "color-contrast": { enabled: false } } });
    expect(r.violations.map((v) => v.id)).toEqual([]);
  });

  it("picks the nearest dot; a tap shows its label and a second tap opens it", () => {
    const group = chart();
    // jsdom measures nothing: the chart lays out at its default 320px.
    group.getBoundingClientRect = () =>
      ({ left: 0, top: 0, width: 320, height: 60, right: 320, bottom: 60 }) as DOMRect;
    const L = histLayout(POINTS, 320, 16, ["d"]);
    const e = L.dots.find((d) => d.p.id === "e")!;
    fireEvent.pointerMove(group, { pointerType: "mouse", clientX: e.cx + 6, clientY: e.cy - 8 });
    expect(label()).toHaveAttribute("href", "/politicians/e");
    fireEvent.pointerLeave(group, { pointerType: "mouse" });
    fireEvent.pointerDown(group, { pointerType: "touch" });
    const a = L.dots.find((d) => d.p.id === "a")!;
    fireEvent.click(group, { clientX: a.cx, clientY: a.cy });
    expect(label()).toHaveAttribute("href", "/politicians/a");
    const opened = vi.fn((ev: MouseEvent) => ev.preventDefault());
    label()!.addEventListener("click", opened);
    fireEvent.click(label()!);
    expect(opened).toHaveBeenCalledOnce();
  });
});
