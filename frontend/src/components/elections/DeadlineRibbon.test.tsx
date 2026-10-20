import { describe, expect, it } from "vitest";
import { render } from "@testing-library/react";
import DeadlineRibbon from "./DeadlineRibbon";
import type { VoterDeadline } from "@/types/election";

const deadline = (over: Partial<VoterDeadline>): VoterDeadline =>
  ({
    kind: "ballot_return",
    method: "mail",
    basis: "received",
    label: "",
    rule: "",
    date: "2026-10-20",
    endDate: null,
    status: "open",
    closesOn: "2026-10-20",
    ...over,
  }) as VoterDeadline;

describe("DeadlineRibbon", () => {
  it("ends a window that runs past election day at the track's end", () => {
    const { container } = render(
      <DeadlineRibbon
        items={[deadline({ date: "2026-10-14", endDate: "2026-11-13" })]}
        today="2026-10-16"
        electionDay="2026-11-03"
        registerBy={null}
      />
    );
    for (const band of container.querySelectorAll<HTMLElement>("span[style*='width']")) {
      const right = parseFloat(band.style.left) + parseFloat(band.style.width);
      expect(right).toBeLessThanOrEqual(100.0001);
    }
  });
});
