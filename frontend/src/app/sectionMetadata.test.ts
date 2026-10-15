// @vitest-environment node
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { describe, expect, it } from "vitest";
import { metadata as politician } from "./politicians/[id]/layout";
import { metadata as bill } from "./congress/bills/[id]/layout";
import { metadata as issue } from "./issue/[id]/layout";
import { metadata as stateBallot } from "./elections/states/[state]/layout";
import { metadata as stateMap } from "./elections/redistricting/[state]/layout";

const APP = __dirname;

function walk(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    return statSync(path).isDirectory() ? walk(path) : [path];
  });
}

/** A dynamic segment ([id], [state]...) somewhere beneath `dir`. */
function hasDynamicChild(dir: string): boolean {
  return readdirSync(dir).some((name) => {
    const path = join(dir, name);
    if (!statSync(path).isDirectory()) return false;
    return name.startsWith("[") || hasDynamicChild(path);
  });
}

describe("section metadata", () => {
  // A record page's 404 takes its head from the layouts above it, so a
  // section layout's canonical labelled every missing member, bill and
  // state as the section's index page.
  it("no layout with a record route beneath it declares a canonical", () => {
    const offenders = walk(APP)
      .filter((f) => f.endsWith("/layout.tsx"))
      .filter((f) => /pageMetadata\(\{[^)]*\bpath:/.test(readFileSync(f, "utf8")))
      .filter((f) => {
        const dir = f.slice(0, -"/layout.tsx".length);
        // A record route's own layout (explore/[id]) names its own record.
        return !/\/\[[^/]+\]$/.test(dir) && hasDynamicChild(dir);
      })
      .map((f) => relative(APP, f));
    expect(offenders).toEqual([]);
  });

  it("gives each record route's 404 its own title and nothing a found page would inherit", () => {
    for (const m of [politician, bill, issue, stateBallot, stateMap]) {
      expect(Object.keys(m)).toEqual(["title"]);
      expect(String(m.title)).toMatch(/not found|^No /);
    }
  });
});
