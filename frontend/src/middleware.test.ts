// @vitest-environment node
import { describe, expect, it } from "vitest";
import fs from "fs";
import path from "path";
import { config } from "./middleware";

// The middleware counts a visit for any request its matcher admits that looks
// like a page load, and a fetch without Sec-Fetch-Dest (a feed reader, a chat
// bot unfurling a link) looks like one. So every file the site serves that
// isn't a page has to be excluded here, or each fetch of it is a "visit".
const matcher = new RegExp(`^${config.matcher[0]}$`);

// Next's file-convention metadata routes in src/app: a static file is served
// under its own name, a generated one (opengraph-image.tsx) without the
// extension.
const METADATA = /^(favicon|icon|apple-icon|opengraph-image|twitter-image)\d*\.\w+$/;
const metadataRoutes = fs
  .readdirSync(path.join(__dirname, "app"))
  .filter((f) => METADATA.test(f) && !/\.test\.\w+$/.test(f))
  .map((f) => "/" + (/\.[jt]sx?$/.test(f) ? f.replace(/\.[jt]sx?$/, "") : f));

const publicEntries = fs
  .readdirSync(path.join(__dirname, "..", "public"))
  .filter((f) => !f.startsWith("."))
  .map(
    (f) =>
      `/${f}` + (fs.statSync(path.join(__dirname, "..", "public", f)).isDirectory() ? "/x" : "")
  );

describe("middleware matcher", () => {
  it("finds the metadata files it guards", () => {
    expect(metadataRoutes).toContain("/apple-icon.png");
    expect(metadataRoutes).toContain("/opengraph-image");
  });

  it.each([
    ...metadataRoutes,
    ...publicEntries,
    "/feed.xml",
    "/feed/senate.xml",
    "/photo/bioguide/S000",
  ])("does not run on %s", (p) => {
    expect(matcher.test(p)).toBe(false);
  });

  it.each(["/", "/feeds", "/politicians/S000148", "/action", "/elections/states/CA"])(
    "runs on the page %s",
    (p) => {
      expect(matcher.test(p)).toBe(true);
    }
  );
});
