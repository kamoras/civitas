// @vitest-environment node
import { describe, expect, it } from "vitest";
import path from "path";
import sharp from "sharp";

// apple-icon.png is icon.svg rendered at 192px: an exact 6x of its 32-unit
// grid, so every edge lands on a pixel and the render is exact. It is the
// raster the site needs where SVG isn't accepted (iOS home screens, the
// feed's <icon>, Discord embeds via feed readers). Re-render it with:
//   node -e "require('sharp')('src/app/icon.svg',{density:432}).resize(192,192).png({compressionLevel:9}).toFile('src/app/apple-icon.png')"
describe("apple-icon.png", () => {
  it("is icon.svg, pixel for pixel", async () => {
    const dir = path.join(__dirname);
    const fromSvg = await sharp(path.join(dir, "icon.svg"), { density: 72 * 6 })
      .resize(192, 192)
      .raw()
      .toBuffer();
    const committed = await sharp(path.join(dir, "apple-icon.png")).raw().toBuffer();
    expect(committed.equals(fromSvg)).toBe(true);
  });
});
