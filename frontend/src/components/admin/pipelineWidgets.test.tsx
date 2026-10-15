import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { PipelineRunDetailCard } from "./pipelineWidgets";

const failed = {
  status: "failed",
  startedAt: "2026-10-15T05:00:00Z",
  completedAt: "2026-10-15T05:26:00Z",
  elapsedSeconds: 1560,
  errorMessage: "Senate pipeline failed — see the run's error detail",
  errorDetail:
    "classify_donors: MemoryError (donor_classifier_ai.py:812 in classify_batch)\n\nTraceback (most recent call last):\n  File ...\nMemoryError",
};

describe("PipelineRunDetailCard", () => {
  it("shows a failed run's cause, with its traceback one click away", () => {
    render(<PipelineRunDetailCard title="Senate: last run" run={failed} />);
    const summary = screen.getByText(/^CAUSE: classify_donors: MemoryError/);
    expect(summary.tagName).toBe("SUMMARY");
    const details = summary.closest("details");
    expect(details?.open).toBe(false);
    expect(details?.querySelector("pre")?.textContent).toContain(
      "Traceback (most recent call last)"
    );
  });

  it("shows no cause for a run that kept none", () => {
    render(
      <PipelineRunDetailCard title="House: last run" run={{ ...failed, errorDetail: null }} />
    );
    expect(screen.queryByText(/^CAUSE:/)).toBeNull();
  });
});
