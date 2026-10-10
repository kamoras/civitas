import { describe, expect, it } from "vitest";
import type { PoliticianCard } from "@/types/politicians";
import { delegationOrder } from "./delegationOrder";

const card = (id: string, branch: PoliticianCard["branch"], district: number | null = null) =>
  ({ id, name: id, branch, district }) as PoliticianCard;

describe("delegationOrder", () => {
  it("puts senators first, then House members by district number", () => {
    const list = [
      card("Zed Rep", "house", 10),
      card("Amy Rep", "house", 2),
      card("Bo Sen", "senate"),
      card("Cy Rep", "house", 1),
      card("Al Sen", "senate"),
    ];
    expect(delegationOrder(list).map((p) => p.id)).toEqual([
      "Al Sen",
      "Bo Sen",
      "Cy Rep",
      "Amy Rep",
      "Zed Rep",
    ]);
  });

  it("does not reorder its input", () => {
    const list = [card("B", "house", 2), card("A", "house", 1)];
    delegationOrder(list);
    expect(list.map((p) => p.id)).toEqual(["B", "A"]);
  });
});
