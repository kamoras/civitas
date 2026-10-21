import { afterEach, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import DevelopersPage from "./page";

vi.mock("@/components/layout/Navbar", () => ({ default: () => <header /> }));
vi.mock("@/components/layout/Footer", () => ({ default: () => <footer /> }));

const csv = (description: string) => ({
  operationId: "senators_csv",
  summary: "Every serving senator's scores, as CSV",
  description,
  tags: ["Senators"],
  responses: { "200": { content: { "text/csv": { schema: { type: "string" } } } } },
});

const spec = {
  info: { title: "Civitas public API", version: "1" },
  tags: [{ name: "Senators" }],
  paths: {
    "/api/public/v1/senators.csv": {
      get: csv("The Senate leaderboard.\n\nUTF-8, one header row. Columns: `rank`: place."),
    },
    "/api/public/v1/senators": {
      get: {
        operationId: "list_senators",
        summary: "Serving senators",
        description: "Every serving senator.\n\nPaged like every list.",
        tags: ["Senators"],
        responses: {},
      },
    },
  },
};

afterEach(() => vi.unstubAllGlobals());

it("keeps a CSV download's format behind its More, and a JSON endpoint's text as it is", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response(JSON.stringify(spec), { status: 200 }))
  );
  render(await DevelopersPage());
  const block = document.getElementById("senators_csv")!;
  expect(block).toHaveTextContent("The Senate leaderboard.");
  const more = block.querySelector("details")!;
  expect(more.querySelector("summary")).toHaveTextContent(
    "CSV format: encoding, licence and every column"
  );
  expect(more).toHaveTextContent("UTF-8, one header row.");
  expect(more.open).toBe(false);
  const json = document.getElementById("list_senators")!;
  expect(json.querySelector("details")).toBeNull();
  expect(screen.getByText("Paged like every list.")).toBeInTheDocument();
});
