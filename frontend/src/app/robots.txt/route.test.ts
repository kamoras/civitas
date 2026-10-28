import { describe, expect, it } from "vitest";
import { robotsTxt } from "./route";

describe("robots.txt", () => {
  const body = robotsTxt();

  it("states the content signals inside the all-agents group", () => {
    expect(body).toMatch(
      /User-Agent: \*\nContent-Signal: search=yes, ai-input=yes, ai-train=yes\n/
    );
  });

  it("never disallows /api/, which the client-rendered pages read", () => {
    expect(body).not.toMatch(/Disallow: \/api/);
    expect(body).toContain("Disallow: /admin");
  });

  it("names the sitemap index", () => {
    expect(body).toMatch(/^Sitemap: https?:\/\/\S+\/sitemap-index\.xml$/m);
  });
});
