import { describe, expect, it } from "vitest";
import { render } from "@testing-library/react";
import DeadlineRibbon from "./DeadlineRibbon";
import type { VoterDeadline } from "@/types/election";

const band = (date: string, endDate: string) =>
  ({
    kind: "ballot_return",
    method: "mail",
    basis: "received",
    date,
    endDate,
    closesOn: endDate,
    status: "open",
  }) as VoterDeadline;

describe("DeadlineRibbon", () => {
  it("keeps a window that runs past election day inside the track", () => {
    const { container } = render(
      <DeadlineRibbon
        items={[band("2026-10-01", "2026-11-04"), band("2026-11-04", "2026-11-09")]}
        today="2026-10-16"
        electionDay="2026-11-03"
        registerBy={null}
      />
    );
    for (const el of container.querySelectorAll<HTMLElement>("span[style*='width']")) {
      const left = parseFloat(el.style.left);
      const width = parseFloat(el.style.width);
      expect(left + width).toBeLessThanOrEqual(100.0001);
    }
  });
});
