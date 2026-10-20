import { describe, expect, it } from "vitest";
import {
  courtLead,
  currentTermLine,
  heatColor,
  heatDomain,
  HEAT_HIGH,
  HEAT_LOW,
  pairLookup,
  positionDomain,
  signedPosition,
  splitLabel,
  termsPhrase,
} from "./court";
import { DARK_TEXT_SHADES, shade, SPLIT_SHADES } from "@/components/court/DecisionLineups";
import { contrastOn, resolve, ratio as themeRatio } from "./themePalette.testutil";
import type { Court, CourtJustice, CourtPair } from "@/types/court";

const record = (over: Partial<Court["record"]> = {}): Court["record"] => ({
  cases: 176,
  unanimous: 78,
  oneVote: 20,
  unanimousPct: 44,
  oneVotePct: 11,
  terms: [2023, 2024, 2025],
  ...over,
});

describe("words", () => {
  it("names exactly the terms covered, never a window", () => {
    expect(termsPhrase([2025])).toBe("the 2025 term");
    expect(termsPhrase([2025, 2023, 2024])).toBe("the 2023 to 2025 terms");
    expect(termsPhrase([2022, 2025])).toBe("the 2022 and 2025 terms");
    expect(termsPhrase([2021, 2023, 2025])).toBe("the 2021, 2023 and 2025 terms");
    expect(termsPhrase([])).toBe("");
  });

  it("writes a split with an en dash and a position with a true minus", () => {
    expect(splitLabel("6-3")).toBe("6–3");
    expect(signedPosition(-1.844)).toBe("−1.84");
    expect(signedPosition(0.5)).toBe("+0.50");
  });

  it("leads with the unanimous share from the API's counts", () => {
    const lead = courtLead({ record: record() })!;
    expect(lead.figure).toBe("44%");
    expect(lead.headline).toBe(
      "of the Court's decisions in the 2023 to 2025 terms were unanimous."
    );
    expect(lead.detail).toBe(
      "78 of 176 orally argued cases had no dissent. 20 were decided by a single vote (11%)."
    );
    expect(courtLead({ record: record({ cases: 0, unanimousPct: null, terms: [] }) })).toBeNull();
  });

  it("says a new term with no decisions has none, and where the latest are from", () => {
    expect(currentTermLine({ currentTerm: { term: 2026, decided: 0 }, record: record() })).toBe(
      "The 2026 term has no decision on record yet; the latest decisions are from the 2025 term."
    );
    expect(currentTermLine({ currentTerm: { term: 2026, decided: 1 }, record: record() })).toBe(
      "The 2026 term: 1 decision so far."
    );
    expect(
      currentTermLine({ currentTerm: { term: 2026, decided: 0 }, record: record({ terms: [] }) })
    ).toBe("The 2026 term has no decision on record yet.");
  });
});

describe("scales", () => {
  const pairs: CourtPair[] = [
    { a: "x", b: "y", agree: 50, both: 90, share: 55.6 },
    { a: "y", b: "z", agree: 80, both: 90, share: 88.9 },
  ];

  it("looks a pair up either way round", () => {
    const find = pairLookup(pairs);
    expect(find("y", "x")?.agree).toBe(50);
    expect(find("x", "z")).toBeNull();
  });

  it("runs the heat scale from the lowest share's ten to 100", () => {
    expect(heatDomain(pairs)).toEqual([50, 100]);
    expect(heatDomain([])).toEqual([0, 100]);
    expect(heatColor(50, [50, 100])).toBe(`color-mix(in srgb, ${HEAT_HIGH} 0%, ${HEAT_LOW})`);
    expect(heatColor(100, [50, 100])).toBe(`color-mix(in srgb, ${HEAT_HIGH} 100%, ${HEAT_LOW})`);
  });

  for (const theme of ["light", "dark"] as const) {
    it(`keeps every heatmap value label at WCAG AA on its cell, darkest to lightest (${theme})`, () => {
      for (const t of [0, 50, 100]) {
        const cell = heatColor(50 + t / 2, [50, 100]);
        expect(contrastOn("var(--ink-hi)", cell, theme), `${t}%`).toBeGreaterThanOrEqual(4.5);
      }
    });

    it(`keeps every split label at WCAG AA on its shade (${theme})`, () => {
      SPLIT_SHADES.forEach((_, i) => {
        const { background, color } = shade(i);
        expect(
          themeRatio(resolve(color, theme), resolve(background, theme)),
          background
        ).toBeGreaterThanOrEqual(4.5);
      });
    });
  }

  it("takes the darkest shade past the end", () => {
    expect(shade(99)).toEqual(shade(SPLIT_SHADES.length - 1));
    expect(DARK_TEXT_SHADES).toBeLessThan(SPLIT_SHADES.length);
  });

  it("spans every estimate and interval, and always 0", () => {
    const j = (position: number, low: number | null, high: number | null) =>
      ({ mq: { term: 2024, position, low, high } }) as CourtJustice;
    expect(positionDomain([j(-4.2, -4.8, -3.6), j(3.07, 2.5, 3.6)])).toEqual([-5, 4]);
    expect(positionDomain([j(1.2, null, null)])).toEqual([0, 2]);
    expect(positionDomain([])).toEqual([-1, 1]);
  });
});
