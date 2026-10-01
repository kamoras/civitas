import { describe, expect, it } from "vitest";
import {
  codeSpans,
  endpointsByTag,
  nestedObject,
  paramTypeLabel,
  schemaName,
  typeLabel,
  type Spec,
} from "./openapi";

const spec: Spec = {
  info: { title: "T", version: "v1" },
  paths: {
    "/a": { get: { operationId: "a", tags: ["Senators"], responses: {} } },
    "/b": { get: { operationId: "b", tags: ["Search"], responses: {} } },
    "/c": { get: { operationId: "c", tags: ["Senators"], responses: {} } },
  },
  components: { schemas: { PublicSenatorRowSchema: { properties: { id: { type: "string" } } } } },
};

describe("openapi helpers", () => {
  it("groups endpoints by tag, declared tags first and in their order", () => {
    const groups = endpointsByTag({ ...spec, tags: [{ name: "Search", description: "Find" }] });
    expect(groups.map((g) => [g.tag, g.endpoints.map((e) => e.op.operationId)])).toEqual([
      ["Search", ["b"]],
      ["Senators", ["a", "c"]],
    ]);
    expect(groups[0].description).toBe("Find");
  });

  it("states a parameter's type without 'or null', with its range", () => {
    expect(paramTypeLabel({ anyOf: [{ enum: ["D", "R", "I"] }, { type: "null" }] })).toBe(
      "D, R or I"
    );
    expect(paramTypeLabel({ type: "integer", minimum: 1, maximum: 100 })).toBe("integer, 1 to 100");
    expect(paramTypeLabel({ type: "integer", minimum: 1 })).toBe("integer, at least 1");
  });

  it("splits code spans out of docstring prose", () => {
    expect(codeSpans("`rank` is the place")).toEqual([
      { code: true, text: "rank" },
      { code: false, text: " is the place" },
    ]);
  });

  it("names schemas in plain words", () => {
    expect(schemaName("#/components/schemas/PublicSenatorRowSchema")).toBe("Senator row");
    expect(schemaName("FundingSchema")).toBe("Funding");
  });

  it("describes types the way the page reads them", () => {
    expect(typeLabel({ enum: ["D", "R", "I"] })).toBe("D, R or I");
    expect(typeLabel({ anyOf: [{ type: "number" }, { type: "null" }] })).toBe("number, or null");
    expect(
      typeLabel({ type: "array", items: { $ref: "#/components/schemas/PublicSenatorRowSchema" } })
    ).toBe("list of Senator row");
    expect(typeLabel({ type: "object", additionalProperties: { type: "number" } })).toBe(
      "map of number"
    );
  });

  it("finds the object a field holds through lists, refs and nullables", () => {
    const ref = { $ref: "#/components/schemas/PublicSenatorRowSchema" };
    expect(nestedObject({ type: "array", items: ref }, spec)?.properties).toHaveProperty("id");
    expect(nestedObject({ anyOf: [ref, { type: "null" }] }, spec)?.properties).toHaveProperty("id");
    expect(nestedObject({ type: "string" }, spec)).toBeNull();
  });
});
