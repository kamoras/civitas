// WCAG 2.2 AA sweep of a production build: axe-core in a real Chrome, with
// real layout, so colour contrast, target size and reflow are measured as a
// reader gets them (the vitest axe tests run in jsdom, which has no layout).
//
//   node scripts/axe-routes.mjs .lighthouserc.populated.json
//
// Routes come from the given Lighthouse config, so the two gates audit the
// same pages. Each is loaded at 320px (WCAG 1.4.10's reflow width) and at
// 1280px, and fails on any axe violation tagged WCAG 2.0, 2.1 or 2.2 A/AA, or
// on a page wider than a 320px screen. COLOR_SCHEME=dark audits the dark theme. Each route is then loaded again on
// emulated phones (Playwright device descriptors: touch, a mobile user
// agent, isMobile, DPR 2-3), where a page wider than the screen widens the
// layout viewport itself, so `scrollWidth - clientWidth` reads 0 and only a
// comparison with the device's own width catches it (the ballot pages ran
// up to 310px past a phone that way while the 320px desktop pass was
// clean). Starts `npm start` itself unless BASE_URL points at a server that
// is already up.
import { spawn } from "node:child_process";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { chromium, devices } from "playwright-core";

const require = createRequire(import.meta.url);
const AXE = readFileSync(require.resolve("axe-core/axe.min.js"), "utf8");
const TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"];
// AXE_WIDTHS=320,390,1280 adds widths; 320 (the reflow check) and 1280 by default.
const WIDTHS = (process.env.AXE_WIDTHS ?? "320,1280").split(",").map(Number);
const PHONES = {
  "iPhone SE": devices["iPhone SE (3rd gen)"],
  "Android 360": devices["Galaxy S8"],
  "Android 320": {
    ...devices["Pixel 7"],
    viewport: { width: 320, height: 640 },
    screen: { width: 320, height: 640 },
    deviceScaleFactor: 2,
  },
  // The browser's default text size doubled, as a reader sets it: most of
  // what ran past a phone (unbreakable labels, fixed rem columns, a header
  // that wrapped over the page) only did so here.
  "iPhone SE, 200% text": { ...devices["iPhone SE (3rd gen)"], fontSize: 32 },
  // WCAG 1.4.4 on a desktop: the same doubled text at 1280px.
  "Desktop 1280, 200% text": { viewport: { width: 1280, height: 900 }, fontSize: 32 },
};
// Controls nobody types into, so iOS never zooms for them.
const NOT_TYPED_INTO = [
  "checkbox",
  "radio",
  "range",
  "hidden",
  "submit",
  "button",
  "reset",
  "image",
  "color",
  "file",
];

// One style per role (src/lib/typeStyles.ts): the roles' class strings, read
// from the source, and which roles each element may take (the same table as
// src/lib/typeScale.test.ts, which holds the code; this holds the page).
const TYPE = Object.fromEntries(
  [
    ...readFileSync(new URL("../src/lib/typeStyles.ts", import.meta.url), "utf8")
      .match(/export const TYPE = \{([\s\S]*?)\} as const/)[1]
      .matchAll(/^\s+(\w+): "([^"]+)"/gm),
  ].map((m) => [m[1], m[2]])
);
const ROLES_FOR = {
  h1: ["title", "display"],
  h2: ["section", "boxTitle", "kicker", "kickerToned"],
  h3: ["boxTitle", "section", "kicker", "kickerToned", "label"],
  th: ["label", "small"],
};

/** Each h1, h2, h3 and th whose computed face, size and weight is no role
 *  its element may take, measured against probes of the roles on this page
 *  (so each width's phone steps and a doubled text size are allowed for). */
async function offScale(page) {
  return page.evaluate(
    ({ type, rolesFor }) => {
      const main = document.querySelector("main") ?? document.body;
      const look = (el) => {
        const cs = getComputedStyle(el);
        return `${cs.fontFamily.split(",")[0].trim()} ${cs.fontSize} ${cs.fontWeight}`;
      };
      const probe = {};
      for (const [role, cls] of Object.entries(type)) {
        const el = document.createElement("span");
        el.className = cls;
        el.textContent = "x";
        main.appendChild(el);
        probe[role] = look(el);
        el.remove();
      }
      const out = [];
      for (const el of main.querySelectorAll("h1, h2, h3, th")) {
        if (!el.textContent.trim() || el.closest("svg, .sr-only") || !el.getClientRects().length)
          continue;
        const tag = el.tagName.toLowerCase();
        const got = look(el);
        if (!rolesFor[tag].some((r) => probe[r] === got))
          out.push(`<${tag}> "${el.textContent.trim().slice(0, 40)}": ${got}`);
      }
      return out;
    },
    { type: TYPE, rolesFor: ROLES_FOR }
  );
}

const configPath = process.argv[2];
if (!configPath) {
  console.error("usage: node scripts/axe-routes.mjs <lighthouserc.json>");
  process.exit(2);
}
const urls = JSON.parse(readFileSync(configPath, "utf8")).ci.collect.url;
const base = process.env.BASE_URL ?? "http://localhost:3000";
// COLOR_SCHEME=dark audits the night theme (prefers-color-scheme); light is the default.
const SCHEME = process.env.COLOR_SCHEME === "dark" ? "dark" : "light";

async function up(url) {
  try {
    return (await fetch(url)).status < 500;
  } catch {
    return false;
  }
}

let server = null;
if (!(await up(base))) {
  server = spawn("npm", ["start"], { stdio: "ignore", detached: true });
  for (let i = 0; i < 60 && !(await up(base)); i++) await new Promise((r) => setTimeout(r, 1000));
}

const browser = await chromium.launch({
  channel: process.env.CHROME_PATH ? undefined : "chrome",
  executablePath: process.env.CHROME_PATH,
  args: ["--no-sandbox"],
});
const failures = [];

/** axe's WCAG 2.x A/AA violations on the loaded page, as failure lines. */
async function axeViolations(page, where) {
  await page.addScriptTag({ content: AXE });
  const violations = await page.evaluate(
    async (tags) =>
      (await window.axe.run(document, { runOnly: { type: "tag", values: tags } })).violations.map(
        (v) => ({ id: v.id, help: v.help, targets: v.nodes.map((n) => n.target.join(" ")) })
      ),
    TAGS
  );
  for (const v of violations)
    failures.push(`${where}  ${v.id}: ${v.help}\n    ${v.targets.slice(0, 5).join("\n    ")}`);
  return violations;
}

try {
  for (const url of urls) {
    const path = new URL(url).pathname + new URL(url).search;
    for (const width of WIDTHS) {
      const page = await browser.newPage({ viewport: { width, height: 900 }, colorScheme: SCHEME });
      await page.goto(base + path, { waitUntil: "networkidle", timeout: 60_000 });
      const violations = await axeViolations(page, `${path} @${width}px`);
      const loose = await offScale(page);
      for (const l of loose) failures.push(`${path} @${width}px  type scale: ${l}`);
      if (width === 320) {
        const over = await page.evaluate(
          () => document.documentElement.scrollWidth - document.documentElement.clientWidth
        );
        if (over > 0)
          failures.push(`${path} @320px  reflow: page is ${over}px wider than the screen`);
        // iOS zooms the page on focus into a control under 16px (globals.css,
        // "Form controls on a phone"). Size is checked on every control in
        // the page, shown or not; height on the ones laid out.
        const small = await page.evaluate((skip) => {
          const out = [];
          for (const el of document.querySelectorAll("input, select, textarea")) {
            if (skip.includes(el.type)) continue;
            const fs = parseFloat(getComputedStyle(el).fontSize);
            const r = el.getBoundingClientRect();
            const name = el.id || el.name || el.getAttribute("aria-label") || el.tagName;
            if (fs < 16) out.push(`${name}: ${fs}px text`);
            else if (r.width > 0 && r.height < 43.5)
              out.push(`${name}: ${Math.round(r.height)}px tall`);
          }
          return out;
        }, NOT_TYPED_INTO);
        for (const s of small)
          failures.push(`${path} @320px  phone control under 16px or 44px: ${s}`);
      }
      console.log(
        `${path} @${width}px: ${violations.length} violation(s), ${loose.length} off the type scale`
      );
      await page.close();
    }
    for (const [phone, { fontSize, ...device }] of Object.entries(PHONES)) {
      const context = await browser.newContext(device);
      const page = await context.newPage();
      if (fontSize) {
        const cdp = await context.newCDPSession(page);
        await cdp.send("Page.enable");
        await cdp.send("Page.setFontSizes", { fontSizes: { standard: fontSize, fixed: fontSize } });
      }
      await page.goto(base + path, { waitUntil: "networkidle", timeout: 60_000 });
      const vw = device.viewport.width;
      const wide = await page.evaluate((vw) => {
        const past = [];
        for (const e of document.body.querySelectorAll("*")) {
          const r = e.getBoundingClientRect();
          if (r.width && r.right > vw + 1 && getComputedStyle(e).position !== "fixed")
            if (![...e.children].some((c) => c.getBoundingClientRect().right > vw + 1))
              past.push(`${e.tagName.toLowerCase()}.${String(e.className).slice(0, 60)}`);
        }
        const over = Math.max(document.documentElement.scrollWidth, innerWidth) - vw;
        return { over, past: past.slice(0, 5) };
      }, vw);
      if (wide.over > 0)
        failures.push(
          `${path} on ${phone}  reflow: page is ${wide.over}px wider than the screen\n    ${wide.past.join("\n    ")}`
        );
      // Doubled text: axe again, since contrast and target sizes move with it.
      const found = fontSize ? (await axeViolations(page, `${path} on ${phone}`)).length : 0;
      if (fontSize)
        for (const l of await offScale(page))
          failures.push(`${path} on ${phone}  type scale: ${l}`);
      console.log(
        `${path} on ${phone}: ${wide.over > 0 ? `${wide.over}px too wide` : "fits"}` +
          (fontSize ? `, ${found} violation(s)` : "")
      );
      await context.close();
    }
  }
} finally {
  await browser.close();
  if (server) process.kill(-server.pid);
}

if (failures.length) {
  console.error(`\n${failures.length} WCAG 2.2 AA failure(s):\n\n${failures.join("\n")}`);
  process.exit(1);
}
console.log(
  `\nWCAG 2.2 AA: ${urls.length} routes x ${WIDTHS.length} widths, no violations; fits ${Object.keys(PHONES).length} phones.`
);
