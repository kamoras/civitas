import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import axe from "axe-core";
import SearchPage, { generateMetadata } from "./page";

vi.mock("@/components/layout/Navbar", () => ({ default: () => <header /> }));
vi.mock("@/components/layout/Footer", () => ({ default: () => <footer /> }));
const redirect = vi.fn((url: string) => {
  throw new Error(`redirect ${url}`);
});
vi.mock("next/navigation", () => ({
  redirect: (url: string) => redirect(url),
  useRouter: () => ({ push: vi.fn() }),
}));
// next/form needs the app router's context; a plain form is what it renders.
vi.mock("next/form", () => ({
  default: ({
    ref,
    ...props
  }: React.ComponentProps<"form"> & { ref?: React.Ref<HTMLFormElement> }) => (
    <form ref={ref} {...props} />
  ),
}));

const doc = (id: number, title: string) => ({
  id,
  key: `fr-2026-${id}`,
  title,
  summary: "A summary.",
  date: "2026-10-01",
  docType: "Proposed Rule",
  source: "federal_register",
  politicianName: "",
  politicianId: "",
  chamber: "Regulatory",
  agencyName: "Food and Drug Administration",
  url: "",
  commentUrl: "",
  commentsCloseOn: "",
});

const stats = {
  totalDocuments: 12000,
  byType: { "Proposed Rule": 900, "Senate Floor Speech": 4000 },
  byChamber: {},
  openForComment: 40,
  agencies: [
    {
      slug: "food-and-drug-administration",
      name: "Food and Drug Administration",
      depth: 0,
      documents: 300,
    },
  ],
  coverage: [],
};

let asked: string[] = [];

function backend(routes: Record<string, unknown>) {
  asked = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      asked.push(url);
      const path = url.replace(/^https?:\/\/[^/]+/, "");
      const hit = Object.entries(routes).find(([prefix]) => path.startsWith(prefix));
      return hit
        ? new Response(JSON.stringify(hit[1]), { status: 200 })
        : new Response("{}", { status: 404 });
    })
  );
}

const page = async (params: Record<string, string>) =>
  render(await SearchPage({ searchParams: Promise.resolve(params) }));

beforeEach(() => {
  redirect.mockClear();
});
afterEach(() => vi.unstubAllGlobals());

describe("search results: one list", () => {
  const merged = {
    query: "food safety",
    direct: null,
    items: [
      {
        kind: "document",
        group: "Documents",
        label: "Food additive rule",
        context: "Proposed Rule",
        url: "/explore/fr-2026-1",
      },
      {
        kind: "agency",
        group: "Agencies",
        label: "Food Safety and Inspection Service",
        context: "",
        url: "/agencies/fsis",
      },
      {
        kind: "bill",
        group: "Bills",
        label: "H.R. 1",
        context: "Food safety act",
        url: "/congress/bills/HR.1",
      },
    ],
    kinds: [
      { kind: "document", label: "Documents", count: 1 },
      { kind: "agency", label: "Agencies", count: 1 },
      { kind: "bill", label: "Bills", count: 1 },
    ],
    alternatives: [],
    near: [],
  };

  it("lists matches and documents ranked together, each with its kind, under filter chips", async () => {
    backend({ "/api/search?q=food%20safety": merged });
    const { container } = await page({ q: "food safety" });
    const list = screen.getByRole("region", { name: /3 matches for “food safety”/ });
    const links = within(list).getAllByRole("link");
    expect(links.map((a) => a.getAttribute("href"))).toEqual([
      "/explore/fr-2026-1",
      "/agencies/fsis",
      "/congress/bills/HR.1",
      "/search?tab=documents&q=food+safety",
    ]);
    expect(within(links[0]).getByText("Documents")).toBeInTheDocument();
    const chips = within(
      screen.getByRole("navigation", { name: "Filter the results" })
    ).getAllByRole("link");
    expect(chips.map((c) => c.textContent)).toEqual(["All3", "Agencies1", "Bills1", "Documents"]);
    expect(chips[0]).toHaveAttribute("aria-current", "page");
    expect(chips[3]).toHaveAttribute("href", "/search?tab=documents&q=food+safety");
    const { violations } = await axe.run(container, { rules: { region: { enabled: false } } });
    expect(violations.map((v) => v.id)).toEqual([]);
  });

  it("a kind's chip lists that kind alone", async () => {
    backend({ "/api/search?q=food%20safety": merged });
    await page({ q: "food safety", tab: "bill" });
    const list = screen.getByRole("region", { name: /Bills: 1 match/ });
    expect(
      within(list)
        .getAllByRole("link")
        .map((a) => a.getAttribute("href"))
    ).toEqual(["/congress/bills/HR.1"]);
  });

  it("a confident query still goes straight to its page", async () => {
    backend({ "/api/search?q=EPA": { ...merged, direct: "/agencies/epa" } });
    await expect(page({ q: "EPA" })).rejects.toThrow("redirect /agencies/epa");
  });
});

describe("the Documents filter", () => {
  it("is Explore's hybrid ranking (GET /api/explore), not site search's", async () => {
    // The semantic channel lives behind /api/explore (services/explore_search.
    // hybrid_search). Reading the Documents filter from anything else, say
    // /api/search's title matches, would drop it without another test failing.
    backend({
      "/api/explore/stats": stats,
      "/api/explore?": {
        query: "pap smear testing",
        results: [doc(7, "Cytology proficiency testing")],
        count: 1,
      },
      "/api/congress/committee-record": { total: 0 },
    });
    const { container } = await page({
      tab: "documents",
      q: "pap smear testing",
      type: "Proposed Rule",
    });
    const docs = asked.filter((u) => u.includes("/api/explore?"));
    expect(docs).toHaveLength(1);
    const params = new URL(docs[0]).searchParams;
    expect(params.get("q")).toBe("pap smear testing");
    expect(params.get("doc_type")).toBe("Proposed Rule");
    expect(asked.some((u) => u.includes("/api/search"))).toBe(false);
    expect(screen.getByRole("link", { name: /Cytology proficiency testing/ })).toHaveAttribute(
      "href",
      "/explore/fr-2026-7?q=pap%20smear%20testing"
    );
    // The page's search box keeps the filter, so a new search stays in documents.
    const form = screen.getByRole("search");
    expect(form.querySelector('input[name="tab"]')).toHaveValue("documents");
    expect(form.querySelector('input[name="type"]')).toHaveValue("Proposed Rule");
    const { violations } = await axe.run(container, { rules: { region: { enabled: false } } });
    expect(violations.map((v) => v.id)).toEqual([]);
  });

  it("lists every open comment period with no query (the old /explore?comment=open)", async () => {
    backend({
      "/api/explore/stats": stats,
      "/api/explore?": { query: "", results: [doc(8, "Open rule")], count: 1 },
    });
    await page({ tab: "documents", comment: "open" });
    const params = new URL(asked.find((u) => u.includes("/api/explore?"))!).searchParams;
    expect(params.get("commentable")).toBe("true");
    expect(params.get("q")).toBeNull();
    expect(screen.getByRole("heading", { name: /1 open comment period/ })).toBeInTheDocument();
  });

  it("asks for nothing with nothing asked, and is the indexable page then", async () => {
    backend({ "/api/explore/stats": stats });
    await page({ tab: "documents" });
    expect(asked.some((u) => u.includes("/api/explore?"))).toBe(false);
    expect(screen.getByText("Try searching for:")).toBeInTheDocument();
    const bare = await generateMetadata({ searchParams: Promise.resolve({ tab: "documents" }) });
    expect(bare.alternates?.canonical).toBe("/search?tab=documents");
    expect(bare.robots).toBeUndefined();
    const asking = await generateMetadata({
      searchParams: Promise.resolve({ tab: "documents", q: "tariffs" }),
    });
    expect(asking.robots).toEqual({ index: false, follow: true });
  });
});
