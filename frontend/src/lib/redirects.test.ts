import { describe, expect, it } from "vitest";
import nextConfig from "../../next.config.mjs";

// The query conditions this file's redirects use (no host or header ones).
type Condition = { type: string; key: string; value?: string };

type Rule = {
  source: string;
  destination: string;
  permanent: boolean;
  has?: Condition[];
  missing?: Condition[];
};

const rules = (await nextConfig.redirects!()) as Rule[];

/** The first rule Next would apply to `url` (static sources only, which is
 *  every rule here but /bills/:id), and where it sends it: the
 *  destination's query with the request's carried over, as Next does. */
function follow(url: string): { to: string; permanent: boolean } | null {
  const u = new URL(url, "http://x");
  const query = (c: { key: string; value?: string }) =>
    u.searchParams.has(c.key) && (c.value === undefined || u.searchParams.get(c.key) === c.value);
  const rule = rules.find(
    (r) => r.source === u.pathname && (r.has ?? []).every(query) && !(r.missing ?? []).some(query)
  );
  if (!rule) return null;
  const to = new URL(rule.destination, "http://x");
  for (const [k, v] of u.searchParams) if (!to.searchParams.has(k)) to.searchParams.append(k, v);
  return { to: to.pathname + to.search, permanent: rule.permanent };
}

describe("redirects", () => {
  it.each([
    ["/politicians", "/congress/members"],
    ["/politicians?branch=senate&state=OH", "/congress/members?branch=senate&state=OH"],
    ["/politicians?state=OR", "/congress/members?state=OR"],
    ["/politicians?branch=scotus", "/court?branch=scotus"],
    ["/politicians?branch=president", "/executive/presidents?branch=president"],
    ["/scorecard?branch=president", "/executive/presidents?branch=president"],
    ["/leaderboard?branch=president", "/executive/presidents?branch=president"],
    ["/scorecard", "/congress/members"],
    ["/scorecard?branch=house&state=TX", "/congress/members?branch=house&state=TX"],
    ["/congress/members?chamber=house", "/congress/members/measures?chamber=house"],
    ["/leaderboard", "/congress/members/measures"],
    ["/leaderboard?branch=house", "/congress/members/measures?chamber=house&branch=house"],
    ["/leaderboard?branch=scotus", "/court?branch=scotus"],
  ])("sends %s to %s, permanently", (from, to) => {
    expect(follow(from)).toEqual({ to, permanent: true });
  });

  it("leaves the directory's own addresses alone", () => {
    expect(follow("/congress/members")).toBeNull();
    expect(follow("/congress/members?branch=house&state=OH")).toBeNull();
    expect(follow("/congress/members/measures?chamber=senate")).toBeNull();
  });

  it("never chains: every destination is a page, not another redirect", () => {
    for (const r of rules) {
      if (r.source.includes(":")) continue;
      const query = (r.has ?? []).map((c) => `${c.key}=${c.value ?? "house"}`).join("&");
      const hop = follow(query ? `${r.source}?${query}` : r.source);
      expect(hop, r.source).not.toBeNull();
      expect(follow(hop!.to), `${r.source} -> ${hop!.to}`).toBeNull();
    }
  });
});
