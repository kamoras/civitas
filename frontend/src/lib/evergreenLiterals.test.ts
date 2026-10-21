/**
 * No page is about this Congress (AGENTS.md §6a). Scans every user-visible
 * string in src (JSX text, string and template literals; comments are not
 * read) for a Congress written as an ordinal ("119th"), and the code for a
 * Congress number compared or assigned ("congress === 119"). The sitting
 * Congress comes from the clock (`currentCongress()`, `congressOrdinal`) or
 * the API's own field.
 *
 * The files below may name one, each for a stated reason: a dated record of
 * what was measured or changed, which stays true.
 */
import fs from "node:fs";
import path from "node:path";
import ts from "typescript";
import { describe, expect, it } from "vitest";

const SRC = path.resolve(__dirname, "..");

const ALLOWED: Record<string, string> = {
  "lib/scoreVersions.ts": "the method changelog: what each version measured, when",
  "components/elections/map/ElectionMapMethod.tsx":
    "the fit's training data: past cycles on past lines",
  "app/about/data/page.tsx": 'measurements dated in the sentence ("Measured in October 2026")',
  "app/about/scores/page.tsx": "a validation against a published Congress's coding",
  "components/about/references.tsx": "bibliography: titles of published works",
};

const ORDINAL = /\b1[0-3]\d(?:st|nd|rd|th)\b/;
const CONGRESS_NUMBER = /congress\w*\s*(?:===?|!==?|<=?|>=?|=|:)\s*1[0-3]\d\b/i;

function sourceFiles(dir: string): string[] {
  return fs.readdirSync(dir).flatMap((name) => {
    const p = path.join(dir, name);
    if (fs.statSync(p).isDirectory()) return sourceFiles(p);
    return /\.tsx?$/.test(name) && !/\.test\.tsx?$/.test(name) && !/fixture/.test(name) ? [p] : [];
  });
}

function findings(file: string): string[] {
  const text = fs.readFileSync(file, "utf8");
  const src = ts.createSourceFile(
    file,
    text,
    ts.ScriptTarget.Latest,
    true,
    file.endsWith("x") ? ts.ScriptKind.TSX : ts.ScriptKind.TS
  );
  const found: string[] = [];
  const visit = (node: ts.Node) => {
    if (
      ts.isJsxText(node) ||
      ts.isStringLiteral(node) ||
      ts.isNoSubstitutionTemplateLiteral(node) ||
      ts.isTemplateHead(node) ||
      ts.isTemplateMiddle(node) ||
      ts.isTemplateTail(node)
    ) {
      const raw = node.getText(src);
      if (ORDINAL.test(raw)) found.push(raw.trim().slice(0, 80));
    } else if (
      ts.isBinaryExpression(node) ||
      ts.isVariableDeclaration(node) ||
      ts.isPropertyAssignment(node)
    ) {
      const raw = node.getText(src);
      if (!raw.includes("\n") && CONGRESS_NUMBER.test(raw)) found.push(raw.slice(0, 80));
    }
    ts.forEachChild(node, visit);
  };
  visit(src);
  return found;
}

describe("evergreen literals", () => {
  it("no page names a Congress by number outside the allowlist", () => {
    const bad = sourceFiles(SRC).flatMap((file) => {
      const rel = path.relative(SRC, file).split(path.sep).join("/");
      if (rel in ALLOWED) return [];
      return findings(file).map((f) => `${rel}: ${f}`);
    });
    expect(bad).toEqual([]);
  });

  it("every allowance still excuses something", () => {
    const stale = Object.keys(ALLOWED).filter((rel) => findings(path.join(SRC, rel)).length === 0);
    expect(stale).toEqual([]);
  });
});
