import { afterEach, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import ExecutivePage from "./page";

vi.mock("next/server", () => ({ connection: async () => undefined }));
vi.mock("@/components/layout/Navbar", () => ({ default: () => <header /> }));
vi.mock("@/components/layout/Footer", () => ({ default: () => <footer /> }));
vi.mock("@/components/layout/SectionTabs", () => ({ default: () => <nav /> }));
vi.mock("@/lib/presidentServer", () => ({
  fetchPresidents: async () => [
    { id: "jane-doe-50", name: "Jane Doe", number: 50, termStart: "2029-01-20", isCurrent: true },
  ],
}));
vi.mock("@/lib/agencyServer", () => ({ fetchAgencyIndex: async () => ({ budget: null }) }));
// The hub's cards are an async server component of their own; its figures
// fetch is the real one, against the stubbed backend.
vi.mock("@/components/sections/SectionCards", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/components/sections/SectionCards")>()),
  default: () => null,
}));

const routes: Record<string, unknown> = {
  "/api/action/open-comments": [
    {
      id: 1,
      key: "fr-2029-00001",
      title: "Fees",
      agencyName: "Census Bureau",
      commentsCloseOn: "2029-03-01",
    },
  ],
  "/api/nominations": {
    counts: { pending: 41 },
    items: [
      {
        citation: "PN1-1",
        congress: 121,
        nominees: ["John Roe"],
        positionTitle: "Judge",
        organization: "",
        receivedDate: "2029-02-01",
      },
    ],
  },
  "/api/politicians/jane-doe-50": { governmentRecord: { totalDocs: 212, recentDocs: [] } },
  "/api/sections": {
    agencies: { budget: null, comments: { open: 653 } },
  },
};

afterEach(() => vi.unstubAllGlobals());

it("leads each part of the branch with the backend's figure, its list behind More", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      const path = url.replace(/^https?:\/\/[^/]+/, "");
      const hit = Object.entries(routes).find(([p]) => path.startsWith(p));
      return hit
        ? new Response(JSON.stringify(hit[1]), { status: 200 })
        : new Response("{}", { status: 404 });
    })
  );
  render(await ExecutivePage());
  const box = (id: string) => document.getElementById(id)!.closest("article")!;
  expect(within(box("president-heading")).getByRole("img")).toHaveAccessibleName(
    "212 presidential documents in the Federal Register."
  );
  expect(box("president-heading")).toHaveTextContent("50th president, in office since");
  expect(within(box("comments-heading")).getByRole("img")).toHaveAccessibleName(
    "653 open for public comment today."
  );
  expect(within(box("comments-heading")).getByText("More: three to comment on")).toBeTruthy();
  expect(within(box("nominations-heading")).getByRole("img")).toHaveAccessibleName(
    "41 nominations pending in the Senate."
  );
  expect(box("agencies-heading")).toHaveTextContent("budgets haven't been read yet");
  expect(screen.getByRole("link", { name: "Jane Doe" })).toHaveAttribute(
    "href",
    "/politicians/jane-doe-50"
  );
});
