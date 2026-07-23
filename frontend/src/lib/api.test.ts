import { describe, expect, it } from "vitest";
import { parseExploreSummaryText } from "./api";

describe("parseExploreSummaryText", () => {
  it("splits a complete SUMMARY/KEY POINTS/IMPACT response into its three fields", () => {
    const text = [
      "SUMMARY: The bill funds highway repairs in three states.",
      "KEY POINTS:",
      "- Allocates $2B over five years",
      "- Requires state matching funds",
      "IMPACT: Commuters in affected states see fewer road closures.",
    ].join("\n");

    const result = parseExploreSummaryText(text);
    expect(result.summary).toBe("The bill funds highway repairs in three states.");
    expect(result.keyPoints).toEqual([
      "Allocates $2B over five years",
      "Requires state matching funds",
    ]);
    expect(result.impact).toBe("Commuters in affected states see fewer road closures.");
  });

  it("parses a partial mid-stream chunk (no markers arrived yet) as a bare summary", () => {
    // This is the exact shape the frontend re-parses after every SSE
    // chunk while the LLM is still generating — the marker text hasn't
    // shown up yet, so everything so far is provisional summary text.
    const result = parseExploreSummaryText("SUMMARY: The bill funds highway rep");
    expect(result.summary).toBe("The bill funds highway rep");
    expect(result.keyPoints).toEqual([]);
    expect(result.impact).toBe("");
  });

  it("handles the KEY POINTS marker arriving before IMPACT", () => {
    const result = parseExploreSummaryText("SUMMARY: Text.\nKEY POINTS:\n- Point one\n- Point two");
    expect(result.summary).toBe("Text.");
    expect(result.keyPoints).toEqual(["Point one", "Point two"]);
    expect(result.impact).toBe("");
  });

  it("ignores key-points lines that don't start with a dash", () => {
    const text = "SUMMARY: Text.\nKEY POINTS:\nsome preamble\n- Real point\nIMPACT: X";
    const result = parseExploreSummaryText(text);
    expect(result.keyPoints).toEqual(["Real point"]);
  });

  it("returns all-empty fields for an empty string", () => {
    const result = parseExploreSummaryText("");
    expect(result).toEqual({ summary: "", keyPoints: [], impact: "" });
  });
});
