import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from "vitest";
import {
  EXPLORE_HIGHLIGHT_END,
  EXPLORE_HIGHLIGHT_START,
  __resetApiCache,
  __resetShapeReports,
  fetchActionIssues,
  fetchRecentActionIssues,
  fetchBillsInFlight,
  fetchJusticeLeaderboard,
  fetchLeaderboard,
  fetchMonitors,
  fetchOpenComments,
  fetchPoliticianDirectory,
  fetchPresidentLeaderboard,
  fetchPviMap,
  fetchRepStates,
  fetchSenatorsByState,
  fetchStates,
  fetchTimeline,
  parseExploreSummaryText,
  splitHighlights,
  abortableSleep,
  streamExploreDocumentSummary,
  submitDocumentComment,
  summaryRetryDelayMs,
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

  it("fetchRecentActionIssues hits the is_current-agnostic endpoint with the limit", async () => {
    const fetchMock = mockJson({ issues: [] });
    vi.stubGlobal("fetch", fetchMock);
    await fetchRecentActionIssues(6);
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("/action/issues/recent?limit=6")
    );
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

describe("submitDocumentComment", () => {
  afterEach(() => vi.unstubAllGlobals());

  function respond(status: number, body: unknown) {
    const fetchMock = vi.fn(async () => ({
      ok: status < 400,
      status,
      json: async () => {
        if (body === undefined) throw new SyntaxError("not JSON");
        return body;
      },
    }));
    vi.stubGlobal("fetch", fetchMock);
    return fetchMock;
  }

  it("sends the comment in a JSON body, never the URL", async () => {
    const fetchMock = respond(201, { success: true, message: "ok" });
    await submitDocumentComment(7, "A comment long enough.", "Pat", "");
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/api/explore/7/comments");
    expect(JSON.parse(init.body as string)).toEqual({
      comment: "A comment long enough.",
      name: "Pat",
      organization: "",
    });
  });

  it("surfaces the rate limiter's detail instead of a blank failure", async () => {
    respond(429, { detail: "Too many requests. Try again in a minute." });
    await expect(submitDocumentComment(7, "A comment long enough.")).resolves.toEqual({
      success: false,
      message: "Too many requests. Try again in a minute.",
    });
  });

  it("falls back to a generic message for a non-JSON error page", async () => {
    respond(503, undefined);
    const result = await submitDocumentComment(7, "A comment long enough.");
    expect(result.success).toBe(false);
    expect(result.message).toMatch(/Submission failed/);
  });
});

/** The summary's two requests: the cached read (GET, "none yet" unless
 *  `cached` is given) and the generation (POST, `post`). */
function stubSummaryFetch(
  post: (url: string, init?: RequestInit) => Promise<Response> | undefined,
  cached?: Response
) {
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string, init?: RequestInit) =>
      init?.method === "POST"
        ? post(url, init)
        : Promise.resolve(cached ?? new Response(null, { status: 204 }))
    )
  );
}

describe("streamExploreDocumentSummary", () => {
  afterEach(() => vi.unstubAllGlobals());

  const done = () =>
    new Response('data: {"done": true, "summary": "S", "keyPoints": [], "impact": ""}\n\n', {
      status: 200,
      headers: { "Content-Type": "text/event-stream" },
    });

  it("asks again after a refusal that says to come back, rather than failing", async () => {
    // Another reader's generation of the document was under way; by the
    // retry it is cached.
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response("", { status: 429, headers: { "Retry-After": "10", "X-Summary-Wait": "1" } })
      )
      .mockResolvedValueOnce(new Response("", { status: 503, headers: { "X-Summary-Wait": "1" } }))
      .mockResolvedValueOnce(done());
    stubSummaryFetch(fetchMock);
    const waits: number[] = [];
    const result = await streamExploreDocumentSummary(
      1,
      () => {},
      undefined,
      async (ms) => {
        waits.push(ms);
      }
    );
    expect(result.summary).toBe("S");
    expect(waits).toEqual([10_000, 10_000]);
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it("still fails on a refusal that isn't one to wait out", async () => {
    stubSummaryFetch(vi.fn().mockResolvedValue(new Response("", { status: 404 })));
    await expect(
      streamExploreDocumentSummary(
        1,
        () => {},
        undefined,
        async () => {}
      )
    ).rejects.toThrow("404");
  });

  it("waits out a request that got no response at all", async () => {
    const post = vi
      .fn()
      .mockRejectedValueOnce(new TypeError("Failed to fetch"))
      .mockResolvedValueOnce(done());
    stubSummaryFetch(post);
    const waited: number[] = [];
    const result = await streamExploreDocumentSummary(
      1,
      () => {},
      undefined,
      async (ms) => {
        waited.push(ms);
      }
    );
    expect(result.summary).toBeTruthy();
    expect(post).toHaveBeenCalledTimes(2);
    expect(waited).toHaveLength(1);
  });

  it("reports a request that never gets a response, after a few tries", async () => {
    const post = vi.fn().mockRejectedValue(new TypeError("Failed to fetch"));
    stubSummaryFetch(post);
    await expect(
      streamExploreDocumentSummary(
        1,
        () => {},
        undefined,
        async () => {}
      )
    ).rejects.toThrow("Failed to fetch");
    expect(post).toHaveBeenCalledTimes(4);
  });

  it("counts drops in a row, not across a long wait", async () => {
    const drop = () => Promise.reject(new TypeError("Failed to fetch"));
    const wait = () =>
      Promise.resolve(new Response("busy", { status: 503, headers: { "X-Summary-Wait": "1" } }));
    const post = vi.fn();
    for (const next of [drop, drop, drop, wait, drop, drop, drop])
      post.mockImplementationOnce(next);
    post.mockResolvedValueOnce(done());
    stubSummaryFetch(post);
    const result = await streamExploreDocumentSummary(
      1,
      () => {},
      undefined,
      async () => {}
    );
    expect(result.summary).toBeTruthy();
  });

  it("releases the stream once its final event is read", async () => {
    const res = done();
    const cancel = vi.spyOn(ReadableStreamDefaultReader.prototype, "cancel");
    stubSummaryFetch(vi.fn().mockResolvedValueOnce(res));
    await streamExploreDocumentSummary(1, () => {});
    expect(cancel).toHaveBeenCalled();
    cancel.mockRestore();
  });

  it("reports a stream whose event it can't read, and releases it", async () => {
    // The same answer comes back when asked again: not waited out.
    const cancel = vi.spyOn(ReadableStreamDefaultReader.prototype, "cancel");
    const post = vi.fn().mockResolvedValue(new Response("data: {not json\n\n"));
    stubSummaryFetch(post);
    await expect(
      streamExploreDocumentSummary(
        1,
        () => {},
        undefined,
        async () => {}
      )
    ).rejects.toThrow("unreadable event");
    expect(post).toHaveBeenCalledTimes(1);
    expect(cancel).toHaveBeenCalled();
    cancel.mockRestore();
  });

  it("releases an error response's body", async () => {
    const failed = new Response("oops", { status: 500 });
    const cancel = vi.spyOn(failed.body!, "cancel");
    stubSummaryFetch(vi.fn().mockResolvedValue(failed));
    await expect(streamExploreDocumentSummary(1, () => {})).rejects.toThrow("500");
    expect(cancel).toHaveBeenCalled();
  });

  it("says when it is waiting to be let in, and when the stream starts", async () => {
    const refused = new Response("busy", { status: 503, headers: { "X-Summary-Wait": "1" } });
    stubSummaryFetch(vi.fn().mockResolvedValueOnce(refused).mockResolvedValueOnce(done()));
    const states: boolean[] = [];
    await streamExploreDocumentSummary(
      1,
      () => {},
      undefined,
      async () => {},
      (waiting) => states.push(waiting)
    );
    expect(states).toEqual([true, false]);
  });

  it("releases each refusal's body before waiting", async () => {
    const refused = new Response("busy", { status: 503, headers: { "X-Summary-Wait": "1" } });
    const cancel = vi.spyOn(refused.body!, "cancel");
    stubSummaryFetch(vi.fn().mockResolvedValueOnce(refused).mockResolvedValueOnce(done()));
    await streamExploreDocumentSummary(
      1,
      () => {},
      undefined,
      async () => {}
    );
    expect(cancel).toHaveBeenCalled();
  });

  it("doesn't wait out a refusal the server doesn't mark as a wait", async () => {
    for (const status of [429, 503]) {
      const fetchMock = vi
        .fn()
        .mockResolvedValue(new Response("", { status, headers: { "Retry-After": "5" } }));
      stubSummaryFetch(fetchMock);
      await expect(
        streamExploreDocumentSummary(
          1,
          () => {},
          undefined,
          async () => {}
        )
      ).rejects.toThrow(String(status));
      expect(fetchMock).toHaveBeenCalledTimes(1);
    }
  });

  it("stops waiting and asking once the reader has left", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(
        new Response("", { status: 429, headers: { "Retry-After": "10", "X-Summary-Wait": "1" } })
      );
    stubSummaryFetch(fetchMock);
    const controller = new AbortController();
    const pending = streamExploreDocumentSummary(1, () => {}, controller.signal);
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    controller.abort();
    await expect(pending).rejects.toBeDefined();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});

describe("summaryRetryDelayMs", () => {
  it("follows Retry-After within bounds, with a default when there is none", () => {
    expect(summaryRetryDelayMs("30")).toBe(30_000);
    expect(summaryRetryDelayMs(null)).toBe(10_000);
    expect(summaryRetryDelayMs("")).toBe(10_000);
    expect(summaryRetryDelayMs("0")).toBe(1_000);
    expect(summaryRetryDelayMs("120")).toBe(120_000);
    expect(summaryRetryDelayMs("3600")).toBe(180_000);
  });
});

describe("abortableSleep", () => {
  it("leaves no listener behind on a wait that ends normally", async () => {
    const controller = new AbortController();
    const remove = vi.spyOn(controller.signal, "removeEventListener");
    await abortableSleep(1, controller.signal);
    expect(remove).toHaveBeenCalledWith("abort", expect.any(Function));
  });

  it("ends at once, rejected, when the signal aborts", async () => {
    const controller = new AbortController();
    const waiting = abortableSleep(60_000, controller.signal);
    controller.abort();
    await expect(waiting).rejects.toBeDefined();
  });
});

describe("streamExploreDocumentSummary's result", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("says when the summary was cut short", async () => {
    const body =
      'data: {"done": true, "summary": "S", "keyPoints": [], "impact": "", "partial": true}\n\n';
    stubSummaryFetch(vi.fn().mockResolvedValue(new Response(body, { status: 200 })));
    expect((await streamExploreDocumentSummary(1, () => {})).partial).toBe(true);
  });
});

describe("streamExploreDocumentSummary after its own generation timed out", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("asks again after the hold-off rather than reporting no summary", async () => {
    const sse = (body: string) => new Response(`data: ${body}\n\n`, { status: 200 });
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        sse('{"done": true, "summary": "", "keyPoints": [], "impact": "", "retryAfter": 120}')
      )
      .mockResolvedValueOnce(
        sse('{"done": true, "summary": "S", "keyPoints": [], "impact": "", "truncated": true}')
      );
    stubSummaryFetch(fetchMock);
    const waits: number[] = [];
    const result = await streamExploreDocumentSummary(
      1,
      () => {},
      undefined,
      async (ms) => {
        waits.push(ms);
      }
    );
    expect(result.summary).toBe("S");
    expect(result.truncated).toBe(true);
    expect(waits).toEqual([125_000]); // the whole hold-off, and a margin
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});

describe("streamExploreDocumentSummary when its stream is cut", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("asks again rather than reporting no summary", async () => {
    const cut = new Response('data: {"delta": "SUMMARY: half"}\n\n', { status: 200 });
    const whole = new Response(
      'data: {"done": true, "summary": "S", "keyPoints": [], "impact": ""}\n\n',
      {
        status: 200,
      }
    );
    const fetchMock = vi.fn().mockResolvedValueOnce(cut).mockResolvedValueOnce(whole);
    stubSummaryFetch(fetchMock);
    const seen: string[] = [];
    const result = await streamExploreDocumentSummary(
      1,
      (text) => seen.push(text),
      undefined,
      async () => {}
    );
    expect(result.summary).toBe("S");
    expect(seen).toEqual(["SUMMARY: half", ""]); // the cut text is cleared before asking again
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});

describe("streamExploreDocumentSummary with a summary already made", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("reads it from the API without asking the pipeline", async () => {
    const post = vi.fn();
    stubSummaryFetch(
      post,
      new Response('{"done": true, "summary": "S", "keyPoints": [], "impact": ""}', { status: 200 })
    );
    expect((await streamExploreDocumentSummary(1, () => {})).summary).toBe("S");
    expect(post).not.toHaveBeenCalled();
  });

  it("still asks the pipeline when the cached read fails", async () => {
    const post = vi
      .fn()
      .mockResolvedValue(
        new Response('data: {"done": true, "summary": "S", "keyPoints": [], "impact": ""}\n\n')
      );
    stubSummaryFetch(post, new Response("", { status: 502 }));
    expect((await streamExploreDocumentSummary(1, () => {})).summary).toBe("S");
    expect(post).toHaveBeenCalledTimes(1);
  });

  it("releases a cached read's body it doesn't use", async () => {
    const missing = new Response('{"detail": "none yet"}', { status: 404 });
    const cancel = vi.spyOn(missing.body!, "cancel");
    stubSummaryFetch(
      vi
        .fn()
        .mockResolvedValue(
          new Response('data: {"done": true, "summary": "S", "keyPoints": [], "impact": ""}\n\n')
        ),
      missing
    );
    await streamExploreDocumentSummary(1, () => {});
    expect(cancel).toHaveBeenCalled();
  });
});
