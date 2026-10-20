// @vitest-environment node
import { describe, expect, it } from "vitest";
import path from "path";
import sharp from "sharp";

// The rasters the site needs where SVG isn't accepted (iOS home screens, the
// manifest, the feed's <icon>, Discord embeds via feed readers) are icon.svg
// rendered: apple-icon.png at 192px (6x its 32-unit grid) and icon-512.png
// at 512px (16x), in the light-scheme colours (librsvg ignores the SVG's
// prefers-color-scheme block). Re-render both with:
//   node -e "const s=require('sharp');s('src/app/icon.svg',{density:432}).resize(192,192).png({compressionLevel:9}).toFile('src/app/apple-icon.png');s('src/app/icon.svg',{density:1152}).resize(512,512).png({compressionLevel:9}).toFile('public/icon-512.png')"
// favicon.ico is the same render at 16, 32 and 48px.
const SVG = path.join(__dirname, "icon.svg");

describe.each([
  ["apple-icon.png", path.join(__dirname, "apple-icon.png"), 192],
  ["icon-512.png", path.join(__dirname, "../../public/icon-512.png"), 512],
])("%s", (_name, file, px) => {
  it("is icon.svg, pixel for pixel", async () => {
    const fromSvg = await sharp(SVG, { density: (72 * px) / 32 })
      .resize(px, px)
      .raw()
      .toBuffer();
    const committed = await sharp(file).raw().toBuffer();
    expect(committed.equals(fromSvg)).toBe(true);
  });
});
