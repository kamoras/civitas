import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import BillStageFlow from "./BillStageFlow";

vi.mock("@/hooks/useConfig", () => ({ useConfig: () => null }));

describe("BillStageFlow", () => {
  it("says whether the vetoed filter is on, as every stage filter does", () => {
    const { rerender } = render(
      <BillStageFlow
        stageCounts={{ VETOED: 2 }}
        stageFlow={{}}
        activeStage={null}
        onSelectStage={() => {}}
      />
    );
    const vetoed = screen.getByRole("button", { name: "2 vetoed" });
    expect(vetoed).toHaveAttribute("aria-pressed", "false");
    rerender(
      <BillStageFlow
        stageCounts={{ VETOED: 2 }}
        stageFlow={{}}
        activeStage="VETOED"
        onSelectStage={() => {}}
      />
    );
    expect(vetoed).toHaveAttribute("aria-pressed", "true");
  });

  it("draws each stage from the API's share and width, the share read on a phone too", () => {
    render(
      <BillStageFlow
        stageCounts={{ REFERRED: 90, ENACTED: 1 }}
        stageFlow={{
          REFERRED: { shareText: "99%", barPercent: 100 },
          ENACTED: { shareText: "1%", barPercent: 2 },
        }}
        activeStage={null}
        onSelectStage={() => {}}
      />
    );
    const enacted = screen.getByRole("button", {
      name: /ENACTED\s*1\s*· 1%\s*of all bills counted/,
    });
    expect(enacted.querySelector<HTMLElement>("[style]")!.style.width).toBe("2%");
    expect(enacted).not.toHaveAttribute("title");
  });
});
