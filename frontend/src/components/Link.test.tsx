import { act, render } from "@testing-library/react";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";

const seen: { prefetch: unknown }[] = [];
vi.mock("next/link", () => ({
  default: (props: { prefetch?: unknown; href: string; children: React.ReactNode }) => {
    seen.push({ prefetch: props.prefetch });
    return <a href={props.href}>{props.children}</a>;
  },
}));

import Link from "./Link";

function setBrowser({ webdriver, visibility }: { webdriver: boolean; visibility: string }) {
  Object.defineProperty(navigator, "webdriver", { value: webdriver, configurable: true });
  Object.defineProperty(document, "visibilityState", { value: visibility, configurable: true });
}

const lastPrefetch = () => seen[seen.length - 1].prefetch;

describe("Link", () => {
  afterEach(() => {
    seen.length = 0;
    setBrowser({ webdriver: false, visibility: "visible" });
  });

  it("prefetches as next/link would on a page in view, outside automation", () => {
    setBrowser({ webdriver: false, visibility: "visible" });
    render(<Link href="/congress">Congress</Link>);
    expect(lastPrefetch()).toBeUndefined();
    render(
      <Link href="/search" prefetch={false}>
        Search
      </Link>
    );
    expect(lastPrefetch()).toBe(false);
  });

  it("is a plain link in a browser automation drives", () => {
    setBrowser({ webdriver: true, visibility: "visible" });
    const { getByText } = render(
      <Link href="/congress" prefetch className="x">
        Congress
      </Link>
    );
    // Not next/link at all: a click loads the page, never a router fetch.
    expect(seen).toHaveLength(0);
    const a = getByText("Congress");
    expect(a.getAttribute("href")).toBe("/congress");
    expect(a.getAttribute("class")).toBe("x");
    expect(a.hasAttribute("prefetch")).toBe(false);
  });

  it("waits for a background tab to be shown", () => {
    setBrowser({ webdriver: false, visibility: "hidden" });
    render(<Link href="/congress">Congress</Link>);
    expect(lastPrefetch()).toBe(false);
    act(() => {
      setBrowser({ webdriver: false, visibility: "visible" });
      document.dispatchEvent(new Event("visibilitychange"));
    });
    expect(lastPrefetch()).toBeUndefined();
  });

  it("is the only importer of next/link", () => {
    const offenders: string[] = [];
    const walk = (dir: string) => {
      for (const name of readdirSync(dir)) {
        const path = join(dir, name);
        if (statSync(path).isDirectory()) walk(path);
        else if (
          /\.tsx?$/.test(name) &&
          !path.endsWith(join("components", "Link.tsx")) &&
          !name.includes(".test.") &&
          /from ["']next\/link["']/.test(readFileSync(path, "utf8"))
        )
          offenders.push(path);
      }
    };
    walk(join(__dirname, ".."));
    expect(offenders).toEqual([]);
  });
});
