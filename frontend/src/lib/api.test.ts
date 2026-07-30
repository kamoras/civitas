import { describe, expect, it } from "vitest";
import {
  EXPLORE_HIGHLIGHT_END,
  EXPLORE_HIGHLIGHT_START,
  parseExploreSummaryText,
  splitHighlights,
} from "./api";

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

describe("splitHighlights", () => {
  const S = EXPLORE_HIGHLIGHT_START;
  const E = EXPLORE_HIGHLIGHT_END;

  it("splits a snippet into plain and matched segments", () => {
    expect(splitHighlights(`the agency proposes new ${S}wildfire${E} rules`)).toEqual([
      { text: "the agency proposes new ", match: false },
      { text: "wildfire", match: true },
      { text: " rules", match: false },
    ]);
  });

  it("handles several matches and a leading match", () => {
    expect(splitHighlights(`${S}PFAS${E} and ${S}dioxin${E}`)).toEqual([
      { text: "PFAS", match: true },
      { text: " and ", match: false },
      { text: "dioxin", match: true },
    ]);
  });

  it("treats HTML in the document text as literal text, never markup", () => {
    // Snippets are verbatim slices of government document bodies. The
    // backend marks matches with control characters precisely so this
    // function can exist without dangerouslySetInnerHTML.
    const segments = splitHighlights(`<script>alert(1)</script> ${S}water${E}`);
    expect(segments[0]).toEqual({ text: "<script>alert(1)</script> ", match: false });
    expect(segments[1]).toEqual({ text: "water", match: true });
  });

  it("keeps the term marked when a snippet is truncated mid-highlight", () => {
    // The term really did match; the excerpt was just cut before the
    // closing marker. Dropping the mark would be the wrong repair.
    expect(splitHighlights(`funding for ${S}wildfire`)).toEqual([
      { text: "funding for ", match: false },
      { text: "wildfire", match: true },
    ]);
  });

  it("returns a single plain segment when nothing matched", () => {
    expect(splitHighlights("no markers here")).toEqual([
      { text: "no markers here", match: false },
    ]);
  });

  it("returns nothing for an empty snippet", () => {
    expect(splitHighlights("")).toEqual([]);
  });
});

describe("splitHighlights — unpaired markers", () => {
  const S = EXPLORE_HIGHLIGHT_START;
  const E = EXPLORE_HIGHLIGHT_END;

  it("never leaks a control character into rendered text", () => {
    // FTS5 truncates snippets at token boundaries, which can leave a marker
    // without its partner. An unpaired one left in place renders as an
    // invisible control character inside the excerpt.
    for (const snippet of [`a ${S}b`, `a ${E}b`, `${E}a${E}`, `${S}${S}a`]) {
      for (const segment of splitHighlights(snippet)) {
        expect(segment.text).not.toContain(S);
        expect(segment.text).not.toContain(E);
      }
    }
  });

  it("recovers the text around an unpaired end marker", () => {
    expect(splitHighlights(`funding for ${E}wildfire`)).toEqual([
      { text: "funding for ", match: false },
      { text: "wildfire", match: false },
    ]);
  });
});
