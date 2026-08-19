import { describe, expect, it } from "vitest";
import { usableRecord } from "./ssrPayload";

interface Profile {
  identity: { name: string };
  branch: string;
}

/**
 * The five SSR detail routes guarded with `if (!res.ok) return null` and then
 * `if (!payload) notFound()`, which catches a 500 and a `null` body and misses
 * `{}` — because `{}` is truthy. Verified against a stub answering `{}` to
 * every endpoint: /politicians/[id], /bills/[id], /issue/[id],
 * /elections/[raceId] and /elections/states/[state] all returned HTTP 500 with
 * `Cannot read properties of undefined`. They return 404 and the real
 * "no record at this address" page now.
 */
describe("usableRecord", () => {
  it("rejects the empty object that slipped past every `if (!payload)` guard", () => {
    expect(usableRecord<Profile>({}, "identity", "branch")).toBeNull();
  });

  it("rejects a payload missing one required field", () => {
    expect(usableRecord<Profile>({ branch: "senate" }, "identity", "branch")).toBeNull();
  });

  it("rejects a required field that is present but null", () => {
    // The backend's `_build_scorecard` returns None from a bare `except`, so
    // present-but-null is the shape a half-failed lookup actually produces.
    expect(usableRecord<Profile>({ identity: null, branch: "senate" }, "identity")).toBeNull();
  });

  it.each([null, undefined, [], "a string", 42, true])("rejects a non-record payload: %s", (p) => {
    expect(usableRecord<Profile>(p, "identity")).toBeNull();
  });

  it("passes a complete record through untouched", () => {
    const payload = { identity: { name: "A. Senator" }, branch: "senate" };
    expect(usableRecord<Profile>(payload, "identity", "branch")).toBe(payload);
  });

  it("does not require fields it was not asked about", () => {
    // Optional fields stay optional: `scorecard` is legitimately null for a
    // member the pipeline has not scored yet, and that page renders.
    const payload = { identity: { name: "A" }, branch: "senate", scorecard: null };
    expect(usableRecord<Profile>(payload, "identity", "branch")).toBe(payload);
  });

  it("treats a falsy-but-present value as usable", () => {
    // 0 and "" are real values, not absence — only undefined/null are absence.
    expect(usableRecord<{ count: number }>({ count: 0 }, "count")).not.toBeNull();
    expect(usableRecord<{ title: string }>({ title: "" }, "title")).not.toBeNull();
  });
});
