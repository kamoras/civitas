import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import axe from "axe-core";
import BoxGrid from "./BoxGrid";
import RecordBox from "./RecordBox";

afterEach(cleanup);

async function violations(container: HTMLElement) {
  const result = await axe.run(container, {
    runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"] },
  });
  return result.violations.map((v) => v.id);
}

function grid() {
  return render(
    <BoxGrid columns={3} label="The record" labelId="record">
      <RecordBox
        lead
        id="path"
        kicker="Became law · July 4, 2025"
        mark="§"
        tone="done"
        title="Public Law 119-21, 45 days after it was introduced"
        dek="Introduced May 20, 2025. Became law July 4, 2025."
        figure={{ type: "stat", value: 45, caption: "days, bill to law" }}
        more={{ label: "every step", children: <p>Introduced in the House, May 20, 2025</p> }}
      />
      <RecordBox
        kicker="Summary · Congressional Research Service"
        title="What it does"
        quote={{ text: "This bill reduces taxes.", source: "Congressional Research Service" }}
      />
      <RecordBox
        kicker="House roll call 190"
        tone="record"
        title="The House agreed, 218 to 214"
        href="/congress/votes/house/119/1/190"
        share="vote-190"
        figure={{ type: "split", yeas: 218, nays: 214, yeaShare: 0.5046, caption: "Passed" }}
        links={[
          { label: "Roll call 190", href: "/congress/votes/house/119/1/190" },
          { label: "The Clerk's record", href: "https://clerk.house.gov/evs/2025/roll190.xml" },
        ]}
      />
    </BoxGrid>
  );
}

describe("RecordBox", () => {
  it("draws a kicker, a titled heading, the line and the figure as text", () => {
    grid();
    const lead = screen.getByRole("heading", { name: /Public Law 119-21/ }).closest("article")!;
    expect(lead).toHaveAttribute("id", "path");
    expect(lead.className).toContain("col-span-full");
    expect(within(lead).getByText("Became law · July 4, 2025")).toBeTruthy();
    expect(within(lead).getByText("45")).toBeTruthy();
    expect(within(lead).getByText("days, bill to law")).toBeTruthy();
    // A split is an image that says what it shows.
    expect(screen.getByRole("img", { name: "218 yea to 214 nay. Passed." })).toBeTruthy();
  });

  it("credits a quote to its source, in quotation marks", () => {
    grid();
    const box = screen.getByRole("heading", { name: "What it does" }).closest("article")!;
    expect(within(box).getByText("“This bill reduces taxes.”").closest("blockquote")).toBeTruthy();
    expect(within(box).getByText("Congressional Research Service").tagName).toBe("FIGCAPTION");
  });

  it("keeps the full text in a closed native disclosure", () => {
    grid();
    const summary = screen.getByText(/More: every step/);
    expect(summary.tagName).toBe("SUMMARY");
    expect(summary.closest("details")!.open).toBe(false);
    expect(screen.getByText("Introduced in the House, May 20, 2025")).toBeTruthy();
  });

  it("links the title, marks another site's link and offers a share anchor", () => {
    grid();
    const box = screen.getByRole("heading", { name: /The House agreed/ }).closest("article")!;
    expect(box).toHaveAttribute("data-share-section", "vote-190");
    expect(within(box).getByRole("link", { name: "The House agreed, 218 to 214" })).toHaveAttribute(
      "href",
      "/congress/votes/house/119/1/190"
    );
    const outside = within(box).getByRole("link", { name: /The Clerk's record/ });
    expect(outside).toHaveAttribute("rel", "noopener noreferrer");
    expect(outside.textContent).toContain("↗");
  });

  it("lays the grid out by its own width, not the window's", () => {
    grid();
    const section = screen.getByRole("region", { name: "The record" });
    expect(section.className).toContain("@container");
    expect(section.querySelector(".grid")!.className).toContain("@min-[40rem]:grid-cols-2");
  });

  it("has no axe violations", async () => {
    const { container } = grid();
    expect(await violations(container)).toEqual([]);
  });
});
