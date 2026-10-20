import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import axe from "axe-core";
import DataLicense from "./DataLicense";

describe("DataLicense", () => {
  it("states the data license and each restriction that carries over", async () => {
    const { container } = render(
      <main>
        <DataLicense />
      </main>
    );
    expect(screen.getByRole("link", { name: /CC BY 4\.0/ })).toHaveAttribute(
      "href",
      "https://creativecommons.org/licenses/by/4.0/"
    );
    expect(screen.getByText(/non-commercial only/)).toBeInTheDocument();
    expect(screen.getByText(/CC BY-NC 3\.0 United States/)).toBeInTheDocument();
    expect(screen.getByText(/share-alike/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "GNU AGPL 3.0" })).toBeInTheDocument();
    const r = await axe.run(container, {
      runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"] },
    });
    expect(r.violations.map((v) => v.id)).toEqual([]);
  });
});
