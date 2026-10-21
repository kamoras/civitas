import { describe, expect, it } from "vitest";
import { signedPoints } from "./justices";

describe("signedPoints", () => {
  it("signs a share as points, with a true minus", () => {
    expect(signedPoints(0.063)).toBe("+6.3");
    expect(signedPoints(-0.043)).toBe("−4.3");
    expect(signedPoints(0)).toBe("+0.0");
  });
});
