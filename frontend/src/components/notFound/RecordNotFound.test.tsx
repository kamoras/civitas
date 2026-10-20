import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import axe from "axe-core";
import PoliticianNotFound from "@/app/politicians/[id]/not-found";
import IssueNotFound from "@/app/issue/[id]/not-found";
import ExploreNotFound from "@/app/explore/not-found";
import BillNotFound from "@/app/congress/bills/[id]/not-found";
import NotFound from "@/app/not-found";

vi.mock("@/components/layout/Navbar", () => ({ default: () => <header /> }));
vi.mock("@/components/layout/Footer", () => ({ default: () => <footer /> }));
vi.mock("next/navigation", () => ({ usePathname: () => "/politicians/pat-kelly" }));

afterEach(() => vi.unstubAllGlobals());

describe("record 404s", () => {
  it("offers a mistyped member's closest names, one tap away, then the directory", async () => {
    const fetch = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          suggestions: [
            { id: "pat-kelley", name: "Pat Kelley", role: "Senator", party: "D", state: "AZ" },
          ],
        })
      )
    );
    vi.stubGlobal("fetch", fetch);
    render(<PoliticianNotFound />);
    expect(await screen.findByRole("link", { name: /Pat Kelley/ })).toHaveAttribute(
      "href",
      "/politicians/pat-kelley"
    );
    expect(String(fetch.mock.calls[0][0])).toMatch(/\/politicians\/suggest\?q=pat-kelly$/);
    expect(screen.getByRole("link", { name: /directory/ })).toHaveAttribute(
      "href",
      "/congress/members"
    );
    expect(screen.getByRole("heading", { level: 1, name: "404" })).toBeInTheDocument();
  });

  it("gives each kind of record its own next step", () => {
    const hrefs = (el: React.ReactElement) => {
      const { unmount } = render(el);
      const out = screen.getAllByRole("link").map((a) => a.getAttribute("href"));
      unmount();
      return out;
    };
    expect(hrefs(<IssueNotFound />)).toEqual(["/action?tab=issues", "/action?tab=timeline", "/"]);
    expect(hrefs(<ExploreNotFound />)).toEqual(["/explore", "/"]);
    expect(hrefs(<BillNotFound />)).toEqual(["/congress/bills", "/congress", "/"]);
    expect(hrefs(<NotFound />)).toEqual(["/"]);
  });

  it("has no axe violations", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response('{"suggestions": []}')));
    render(<PoliticianNotFound />);
    const result = await axe.run(document.body, {
      rules: { "color-contrast": { enabled: false } },
    });
    expect(result.violations.map((v) => v.id)).toEqual([]);
  });
});
