import { describe, expect, it } from "vitest";
import { NextRequest } from "next/server";
import { GET } from "./route";

const go = (q: string) =>
  GET(new NextRequest(`https://civitas-research.org/search?q=${encodeURIComponent(q)}`));

describe("GET /search", () => {
  it("opens a bill's page for a bill number", () => {
    const r = go("H.R. 7008");
    expect(r.status).toBe(307);
    expect(r.headers.get("location")).toBe("/congress/bills/HR.7008");
  });

  it("sends anything else to Explore with the query, and an empty one to Explore", () => {
    expect(go("drug prices").headers.get("location")).toBe("/explore?q=drug%20prices");
    expect(go(" ").headers.get("location")).toBe("/explore");
  });
});
