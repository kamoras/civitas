import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import BillStageFlow from "./BillStageFlow";

vi.mock("@/hooks/useConfig", () => ({ useConfig: () => null }));

describe("BillStageFlow", () => {
  it("says whether the vetoed filter is on, as every stage filter does", () => {
    const { rerender } = render(
      <BillStageFlow stageCounts={{ VETOED: 2 }} activeStage={null} onSelectStage={() => {}} />
    );
    const vetoed = screen.getByRole("button", { name: "2 vetoed" });
    expect(vetoed).toHaveAttribute("aria-pressed", "false");
    rerender(
      <BillStageFlow stageCounts={{ VETOED: 2 }} activeStage="VETOED" onSelectStage={() => {}} />
    );
    expect(vetoed).toHaveAttribute("aria-pressed", "true");
  });
});
