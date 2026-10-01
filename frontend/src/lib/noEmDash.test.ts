/**
 * Site prose uses no em dashes (2026-10). Scans every user-visible string in
 * src (JSX text, string and template literals; comments are not read) and
 * fails on an em dash, except a string that is only the dash, the "no
 * data" placeholder in tables. Write a comma, colon, parentheses or a full
 * stop instead; for a separator in a label, the middle dot " · ".
 */
import fs from "node:fs";
import path from "node:path";
import ts from "typescript";
import { describe, expect, it } from "vitest";

const SRC = path.resolve(__dirname, "..");

function sourceFiles(dir: string): string[] {
  return fs.readdirSync(dir).flatMap((name) => {
    const p = path.join(dir, name);
    if (fs.statSync(p).isDirectory()) return sourceFiles(p);
    return /\.tsx?$/.test(name) && !/\.test\.tsx?$/.test(name) ? [p] : [];
  });
}

function emDashes(file: string): string[] {
  const text = fs.readFileSync(file, "utf8");
  if (!text.includes("—")) return [];
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
      const body = raw.replace(/^["'`}]|["'`]$|\$\{$/g, "").trim();
      if (raw.includes("—") && body !== "—") {
        const { line } = src.getLineAndCharacterOfPosition(node.getStart(src));
        found.push(`${path.relative(SRC, file)}:${line + 1}: ${body.slice(0, 80)}`);
      }
    }
    ts.forEachChild(node, visit);
  };
  visit(src);
  return found;
}

describe("site prose", () => {
  it("has no em dashes", () => {
    expect(sourceFiles(SRC).flatMap(emDashes)).toEqual([]);
  });
});
