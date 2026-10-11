/**
 * A visitor's browser never requests an image from another host (AGENTS.md
 * §8): every photo comes through the site's own `/photo/<kind>/<id>` route
 * (lib/photos.ts), which fetches it server-side. This fails on any string
 * in src that is an absolute image URL, or a JSX `src`/`srcSet`/`poster`
 * pointing at one — the backend's half is
 * backend/tests/test_same_origin_photos.py.
 */
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import ts from "typescript";
import { describe, expect, it } from "vitest";

const SRC = path.resolve(__dirname, "..");

// Server-side fetchers: the one place a photo's source URL is written down.
const SERVER_ONLY = new Set(["lib/photos.ts"]);

const IMAGE_URL =
  /https?:\/\/[^\s"'`]+?\.(?:jpe?g|png|gif|webp|avif|svg)\b|bioguide\.congress\.gov\/(?:bioguide\/)?photo|api\.oyez\.org/i;
const IMAGE_ATTRS = new Set(["src", "srcSet", "poster"]);

function sourceFiles(dir: string): string[] {
  return fs.readdirSync(dir).flatMap((name) => {
    const p = path.join(dir, name);
    if (fs.statSync(p).isDirectory()) return sourceFiles(p);
    return /\.tsx?$/.test(name) && !/\.test\.tsx?$/.test(name) ? [p] : [];
  });
}

function thirdPartyImages(file: string): string[] {
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
      ts.isStringLiteral(node) ||
      ts.isNoSubstitutionTemplateLiteral(node) ||
      ts.isTemplateExpression(node)
    ) {
      if (IMAGE_URL.test(node.getText())) found.push(node.getText());
    }
    if (
      ts.isJsxAttribute(node) &&
      IMAGE_ATTRS.has(node.name.getText()) &&
      node.initializer &&
      /^["'{`\s]*https?:/.test(node.initializer.getText())
    ) {
      found.push(node.getText());
    }
    ts.forEachChild(node, visit);
  };
  visit(src);
  return found;
}

describe("images are same-origin", () => {
  it("no page names an image on another host", () => {
    const offenders = sourceFiles(SRC)
      .filter((f) => !SERVER_ONLY.has(path.relative(SRC, f).split(path.sep).join("/")))
      .flatMap((f) => thirdPartyImages(f).map((s) => `${path.relative(SRC, f)}: ${s}`));
    expect(offenders).toEqual([]);
  });

  it("catches one", () => {
    const tmp = path.join(fs.mkdtempSync(path.join(os.tmpdir(), "img-")), "x.tsx");
    try {
      fs.writeFileSync(
        tmp,
        'const a = <img src="https://cdn.example.org/a" />;\nconst b = `https://x.example/${id}.jpg`;\n'
      );
      expect(thirdPartyImages(tmp)).toHaveLength(2);
    } finally {
      fs.rmSync(path.dirname(tmp), { recursive: true });
    }
  });
});
