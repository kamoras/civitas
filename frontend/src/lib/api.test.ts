import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from "vitest";
import {
  EXPLORE_HIGHLIGHT_END,
  EXPLORE_HIGHLIGHT_START,
  __resetApiCache,
  __resetShapeReports,
  fetchActionIssues,
  fetchBillsInFlight,
  fetchJusticeLeaderboard,
  fetchLeaderboard,
  fetchMonitors,
  fetchOpenComments,
  fetchPoliticianDirectory,
  fetchPresidentLeaderboard,
  fetchPviMap,
  fetchRaces,
  fetchRepStates,
  fetchSenatorsByState,
  fetchStates,
  fetchTimeline,
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
    expect(splitHighlights("no markers here")).toEqual([{ text: "no markers here", match: false }]);
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

// ---------------------------------------------------------------------------
// Shape guarantees
//
// The Pi serves this site from a database the pipeline fills in overnight, so
// "the endpoint exists but has nothing in it" is a normal state, not an edge
// case. Each of these fetchers declares a list in its return type; before the
// normalizers, a `{}` from the backend satisfied TypeScript and then crashed
// the caller's `.map` at runtime, replacing the page with the browser's own
// error screen. These tests hold that line.
// ---------------------------------------------------------------------------

const EMPTY_SHAPES: [string, unknown][] = [
  ["an empty object", {}],
  ["a bare array where an object belongs", []],
  ["null", null],
  ["a payload whose list field is null", { issues: null, entries: null, months: null }],
];

function mockJson(body: unknown) {
  return vi.fn(async () => ({
    ok: true,
    status: 200,
    json: async () => body,
  })) as unknown as typeof fetch;
}

describe("API shape guarantees", () => {
  beforeEach(() => {
    __resetApiCache();
    __resetShapeReports();
    vi.spyOn(console, "warn").mockImplementation(() => {});
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  const listFetchers: [string, () => Promise<unknown[]>][] = [
    ["fetchLeaderboard", () => fetchLeaderboard()],
    ["fetchStates", () => fetchStates()],
    ["fetchRepStates", () => fetchRepStates()],
    ["fetchJusticeLeaderboard", () => fetchJusticeLeaderboard()],
    ["fetchPresidentLeaderboard", () => fetchPresidentLeaderboard()],
    ["fetchOpenComments", () => fetchOpenComments()],
    ["fetchRaces", () => fetchRaces()],
    ["fetchPoliticianDirectory", () => fetchPoliticianDirectory()],
    ["fetchSenatorsByState", () => fetchSenatorsByState("CA")],
  ];

  for (const [name, call] of listFetchers) {
    for (const [label, body] of EMPTY_SHAPES) {
      it(`${name} returns a list when the backend sends ${label}`, async () => {
        vi.stubGlobal("fetch", mockJson(body));
        __resetApiCache();
        const result = await call();
        expect(Array.isArray(result)).toBe(true);
        expect(result).toHaveLength(0);
      });
    }
  }

  it("fetchActionIssues always exposes issues and availableDates as lists", async () => {
    vi.stubGlobal("fetch", mockJson({ date: "2026-08-18" }));
    const result = await fetchActionIssues();
    expect(result.issues).toEqual([]);
    expect(result.availableDates).toEqual([]);
    // Fields the backend did send are preserved, not clobbered by the defaults.
    expect(result.date).toBe("2026-08-18");
  });

  it("fetchTimeline always exposes its four lists", async () => {
    vi.stubGlobal("fetch", mockJson({ year: 2026, totalDays: 4 }));
    const result = await fetchTimeline();
    expect(result.months).toEqual([]);
    expect(result.monitors).toEqual([]);
    expect(result.topThemes).toEqual([]);
    expect(result.upcomingEvents).toEqual([]);
    expect(result.totalDays).toBe(4);
  });

  it("fetchMonitors returns { monitors: [] } for an empty payload", async () => {
    vi.stubGlobal("fetch", mockJson({}));
    expect(await fetchMonitors()).toEqual({ monitors: [] });
  });

  it("fetchBillsInFlight returns an empty bills list rather than undefined", async () => {
    vi.stubGlobal("fetch", mockJson({ total: 0 }));
    expect((await fetchBillsInFlight()).bills).toEqual([]);
  });

  it("fetchBillsInFlight returns a reducible stageCounts map", async () => {
    vi.stubGlobal("fetch", mockJson({ total: 0 }));
    // The bills page reduces over this during render to headline a bill count.
    expect((await fetchBillsInFlight()).stageCounts).toEqual({});
  });

  it("fetchPviMap returns indexable maps even with nothing in them", async () => {
    vi.stubGlobal("fetch", mockJson({}));
    const pvi = await fetchPviMap();
    // The elections map indexes these by state/district code on every render.
    expect(pvi.states).toEqual({});
    expect(pvi.districts).toEqual({});
  });

  it("keeps real data intact — normalizing is not filtering", async () => {
    const entry = { id: "S001", name: "Example", overallScore: 71 };
    vi.stubGlobal("fetch", mockJson([entry]));
    expect(await fetchLeaderboard()).toEqual([entry]);
  });
});

describe("shape corrections are reported, not swallowed", () => {
  // Coercing silently is right for the reader and wrong for whoever maintains
  // the backend: an endpoint returning garbage would look exactly like an
  // endpoint with nothing in it yet. These hold the developer-facing signal.
  let warn: MockInstance<(...args: unknown[]) => void>;

  beforeEach(() => {
    __resetApiCache();
    __resetShapeReports();
    warn = vi.spyOn(console, "warn").mockImplementation(() => {}) as unknown as MockInstance<
      (...args: unknown[]) => void
    >;
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("names the endpoint and what arrived instead", async () => {
    vi.stubGlobal("fetch", mockJson({ not: "a list" }));
    await fetchLeaderboard();
    expect(warn).toHaveBeenCalledTimes(1);
    const message = String(warn.mock.calls[0][0]);
    expect(message).toContain("/senators/leaderboard");
    expect(message).toContain("got object");
  });

  it("names the offending field on an object payload", async () => {
    vi.stubGlobal("fetch", mockJson({ date: "2026-08-18", issues: "nope" }));
    await fetchActionIssues();
    const messages = warn.mock.calls.map((c: unknown[]) => String(c[0]));
    expect(messages.some((m) => m.includes('"issues"') && m.includes("got string"))).toBe(true);
  });

  it("reports each field once, not once per poll", async () => {
    vi.stubGlobal("fetch", mockJson({}));
    // The dashboard pollers hit some of these every three seconds; a warning
    // per response would bury the console rather than inform it.
    for (let i = 0; i < 5; i++) {
      __resetApiCache();
      await fetchLeaderboard();
    }
    expect(warn).toHaveBeenCalledTimes(1);
  });

  it("says nothing when the payload is the right shape", async () => {
    vi.stubGlobal("fetch", mockJson([{ id: "S001" }]));
    await fetchLeaderboard();
    expect(warn).not.toHaveBeenCalled();
  });

  it("says nothing for a legitimately empty list", async () => {
    vi.stubGlobal("fetch", mockJson([]));
    await fetchLeaderboard();
    expect(warn).not.toHaveBeenCalled();
  });
});
