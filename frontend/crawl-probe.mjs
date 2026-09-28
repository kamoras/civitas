import { chromium } from "/opt/node22/lib/node_modules/playwright/index.mjs";
import fs from "node:fs";
const axe = fs.readFileSync("node_modules/axe-core/axe.min.js", "utf8");
const BASE = "http://127.0.0.1:3000";
const browser = await chromium.launch({ executablePath: "/opt/pw-browsers/chromium" });
const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
await ctx.route("**/*", (r) => r.request().url().startsWith(BASE) ? r.continue() : r.abort());
const page = await ctx.newPage();
const queue = ["/"], seen = new Set(["/"]);
const problems = [];
let current = "";
page.on("console", (m) => { const t = m.text(); if (m.type() === "error" && !t.includes("ERR_FAILED") && !t.includes("net::")) problems.push(`${current} CONSOLE ${t.slice(0, 200)}`); });
page.on("pageerror", (e) => problems.push(`${current} PAGEERROR ${String(e).slice(0, 200)}`));
page.on("response", (r) => { const u = r.url(); if (u.startsWith(BASE) && r.status() >= 400 && !u.includes("/photo/")) problems.push(`${current} HTTP ${r.status()} ${u.replace(BASE, "")}`); });
const bucket = (k) => { const p = k.split("?")[0].split("/"); return p.length > 3 ? p.slice(0, 3).join("/") : k.split("?")[0]; };
const count = new Map();
let n = 0;
while (queue.length && n < 160) {
  const path = queue.shift(); current = path; n++;
  let resp;
  try { resp = await page.goto(BASE + path, { waitUntil: "load", timeout: 30000 }); await page.waitForLoadState("networkidle", { timeout: 8000 }).catch(() => {}); }
  catch (e) { problems.push(`${path} NAV ${String(e).split("\n")[0].slice(0, 100)}`); continue; }
  if (resp && resp.status() >= 400) problems.push(`${path} STATUS ${resp.status()}`);
  await page.waitForTimeout(300);
  try {
    await page.addScriptTag({ content: axe });
    const v = await page.evaluate(async () => (await axe.run(document, { runOnly: ["wcag2a", "wcag2aa", "wcag21aa"] })).violations.map(v => v.id + "(" + v.nodes.length + "): " + v.nodes[0].target.join(" ")));
    for (const x of v) problems.push(`${path} AXE ${x}`);
  } catch {}
  const links = await page.$$eval("a[href]", (as) => as.map((a) => a.getAttribute("href")));
  for (const h of links) {
    if (!h || /^(mailto|tel):/.test(h) || h.startsWith("#")) continue;
    let u; try { u = new URL(h, BASE + path); } catch { continue; }
    if (u.origin !== BASE || u.pathname.startsWith("/api") || u.pathname.startsWith("/admin") || u.pathname.startsWith("/photo")) continue;
    u.hash = "";
    const k = u.pathname + u.search;
    const b = bucket(k);
    if (!seen.has(k) && (count.get(b) ?? 0) < 4) { seen.add(k); count.set(b, (count.get(b) ?? 0) + 1); queue.push(k); }
  }
}
console.log("visited", n, "left", queue.length);
console.log([...new Set(problems)].join("\n") || "no problems");
await browser.close();
