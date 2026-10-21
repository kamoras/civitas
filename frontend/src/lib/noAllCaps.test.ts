/**
 * No all-caps text (2026-10). Labels, buttons, links, nav items, tabs, tags
 * and table headers are in sentence case; the evidence and the one exception
 * are in `typeStyles.ts`.
 *
 * - The Tailwind `uppercase` class appears only in `typeStyles.ts` (EYEBROW).
 * - JSX text and string literals carry no word typed in capitals unless it
 *   is an acronym or identifier (listed below) or the logotype's two words
 *   in the header, or a quotation of a record as printed. A screen reader may spell typed capitals out letter by
 *   letter; CSS capitals are read as words.
 */
import fs from "node:fs";
import path from "node:path";
import ts from "typescript";
import { describe, expect, it } from "vitest";

const SRC = path.resolve(__dirname, "..");
const ALLOWED_UPPERCASE_CLASS = new Set(["lib/typeStyles.ts"]);
// The lockup: "CIVITAS | RESEARCH" is the logotype (header, and the share cards' copy
// of it, which they take from ogLockup.tsx: their own text is scanned like a page's).
const LOGOTYPE_FILES = new Set(["components/layout/Navbar.tsx", "lib/ogLockup.tsx"]);
// The share cards, where labels used to be put in capitals by code rather than typed.
const CARD_FILES = ["app/opengraph-image.tsx", "app/api/og/route.tsx"];

// Acronyms and identifiers that keep their capitals. Anything else typed in
// capitals is a word in caps.
const ACRONYMS = new Set(
  `API AGPL ARIA BEA BLS BBC BSKY CBO CNN CORS CQ CRS CSS CSV DFL DNL DOM EPA FBI FDA FDR FEC FIPS FRED GDP GET GMT GNU GOP GPO GPT HTML HTTP HTTPS ICU IEEE IRS ISO JSON KHTML LDA LES LLC LLM MCP MEDSL MIT NASS NLP NORC NPR OGE OMB PAC PACS PBS PDF PFAS PNG POST PVI RSS SCOTUS SEC SHA SIC SIL SVG TTFB USA USDA USPS UTC UTF VEST WCAG XML YYYY ZIP STOCK EVEN HJRES SJRES HRES SRES HCONRES SCONRES DEM REP IND LIB OTH SSRS CLDR NFD EMNLP SIGIR ACL ACM BERT APCA OFL OKLCH CIE JPEG ECMA LCP SSR RSC JSX TSX USB GOV CAGR NRSC SBE EAC SOS SOTS PSC MON AXIS OCR INC README SSD XXV ARM LICENSE TIT CPU ETA CTR SVD UCSB AGENTS HHI JFK CFR`.split(
    /\s+/
  )
);

function sourceFiles(dir: string): string[] {
  return fs.readdirSync(dir).flatMap((name) => {
    const p = path.join(dir, name);
    if (fs.statSync(p).isDirectory()) return sourceFiles(p);
    return /\.tsx?$/.test(name) && !/\.test(util)?\.tsx?$/.test(name) ? [p] : [];
  });
}

function capsWords(text: string): string[] {
  // A quotation of a record as printed ("HONEYWELL INTL INC") keeps its case.
  const unquoted = text.replace(/&ldquo;[^]*?&rdquo;|“[^”]*”/g, "");
  return [...unquoted.matchAll(/(?<![A-Za-z0-9_$.+-])[A-Z][A-Z'’]{2,}(?![A-Za-z0-9_$(-])/g)]
    .map((m) => m[0].replace(/['’]S$/, ""))
    .filter((w) => !ACRONYMS.has(w));
}

function scan(file: string): { classes: string[]; words: string[] } {
  const rel = path.relative(SRC, file);
  const text = fs.readFileSync(file, "utf8");
  const src = ts.createSourceFile(
    file,
    text,
    ts.ScriptTarget.Latest,
    true,
    file.endsWith("x") ? ts.ScriptKind.TSX : ts.ScriptKind.TS
  );
  const classes: string[] = [];
  const words: string[] = [];
  const at = (node: ts.Node) =>
    `${rel}:${src.getLineAndCharacterOfPosition(node.getStart(src)).line + 1}`;
  const visit = (node: ts.Node) => {
    const literal =
      ts.isStringLiteral(node) ||
      ts.isNoSubstitutionTemplateLiteral(node) ||
      ts.isTemplateHead(node) ||
      ts.isTemplateMiddle(node) ||
      ts.isTemplateTail(node);
    if (literal || ts.isJsxText(node)) {
      const body = literal
        ? node.getText(src).replace(/^["'`}]|["'`]$|\$\{$/g, "")
        : node.getText(src);
      if (literal && /(^|[\s:])uppercase(\s|$)/.test(body) && !ALLOWED_UPPERCASE_CLASS.has(rel)) {
        classes.push(`${at(node)}: ${body.slice(0, 80)}`);
      }
      // An identifier-like value (SCREAMING_CASE, an enum code, a parser's *_MARKER
      // for text a model writes) is data, not text.
      const parent = node.parent;
      const marker = ts.isVariableDeclaration(parent) && /_MARKER$/.test(parent.name.getText(src));
      const isCode = literal && (/^[A-Z0-9_:-]*$/.test(body) || marker);
      if (!isCode && !LOGOTYPE_FILES.has(rel)) {
        const caps = capsWords(body);
        if (caps.length) words.push(`${at(node)}: ${caps.join(" ")}`);
      }
    }
    ts.forEachChild(node, visit);
  };
  visit(src);
  return { classes, words };
}

describe("letter case", () => {
  const results = sourceFiles(SRC).map(scan);
  it("uses the class only for the shared eyebrow", () => {
    expect(results.flatMap((r) => r.classes)).toEqual([]);
  });
  it("has no words typed in capitals", () => {
    expect(results.flatMap((r) => r.words)).toEqual([]);
  });
  it("puts no share-card text in capitals by code", () => {
    const found = CARD_FILES.flatMap((rel) =>
      fs
        .readFileSync(path.join(SRC, rel), "utf8")
        .split("\n")
        .flatMap((line, i) =>
          // A state code is an identifier, normalised before it is looked up.
          /toUpperCase\(|textTransform/.test(line) && !/rawState\.toUpperCase\(\)/.test(line)
            ? [`${rel}:${i + 1}: ${line.trim()}`]
            : []
        )
    );
    expect(found).toEqual([]);
  });
});
