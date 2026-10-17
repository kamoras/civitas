// WCAG 2.2 AA sweep of a production build: axe-core in a real Chrome, with
// real layout, so colour contrast, target size and reflow are measured as a
// reader gets them (the vitest axe tests run in jsdom, which has no layout).
//
//   node scripts/axe-routes.mjs .lighthouserc.populated.json
//
// Routes come from the given Lighthouse config, so the two gates audit the
// same pages. Each is loaded at 320px (WCAG 1.4.10's reflow width) and at
// 1280px, and fails on any axe violation tagged WCAG 2.0, 2.1 or 2.2 A/AA, or
// on a page wider than a 320px screen. Each route is then loaded again on
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
const WIDTHS = [320, 1280];
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
};

const configPath = process.argv[2];
if (!configPath) {
  console.error("usage: node scripts/axe-routes.mjs <lighthouserc.json>");
  process.exit(2);
}
const urls = JSON.parse(readFileSync(configPath, "utf8")).ci.collect.url;
const base = process.env.BASE_URL ?? "http://localhost:3000";

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
try {
  for (const url of urls) {
    const path = new URL(url).pathname + new URL(url).search;
    for (const width of WIDTHS) {
      const page = await browser.newPage({ viewport: { width, height: 900 } });
      await page.goto(base + path, { waitUntil: "networkidle", timeout: 60_000 });
      await page.addScriptTag({ content: AXE });
      const violations = await page.evaluate(
        async (tags) =>
          (
            await window.axe.run(document, { runOnly: { type: "tag", values: tags } })
          ).violations.map((v) => ({
            id: v.id,
            help: v.help,
            targets: v.nodes.map((n) => n.target.join(" ")),
          })),
        TAGS
      );
      for (const v of violations)
        failures.push(
          `${path} @${width}px  ${v.id}: ${v.help}\n    ${v.targets.slice(0, 5).join("\n    ")}`
        );
      if (width === 320) {
        const over = await page.evaluate(
          () => document.documentElement.scrollWidth - document.documentElement.clientWidth
        );
        if (over > 0)
          failures.push(`${path} @320px  reflow: page is ${over}px wider than the screen`);
      }
      console.log(`${path} @${width}px: ${violations.length} violation(s)`);
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
      console.log(`${path} on ${phone}: ${wide.over > 0 ? `${wide.over}px too wide` : "fits"}`);
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
