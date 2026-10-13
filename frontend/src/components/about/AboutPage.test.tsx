import { render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { AboutPage, Item, List, More, P, Point, Section, Summary } from "./AboutPage";

vi.mock("@/components/layout/Navbar", () => ({ default: () => <header /> }));
vi.mock("@/components/layout/Footer", () => ({ default: () => <footer /> }));
vi.mock("next/navigation", () => ({ usePathname: () => "/about/scores" }));

describe("Item", () => {
  it("lets a wide child scroll inside its own box instead of widening the page", () => {
    // A flex item's minimum width defaults to its content's width, so a
    // <pre className="overflow-x-auto"> inside an Item (the MCP config on
    // /developers) pushed the page to 449px on a 375px phone. jsdom does no
    // layout, so this pins the class that gives the browser leave to shrink
    // the item; the production build was measured at 375px.
    render(
      <List>
        <Item label="A configuration file">
          <pre data-testid="wide" className="overflow-x-auto">
            {"x".repeat(200)}
          </pre>
        </Item>
      </List>
    );
    const content = screen.getByTestId("wide").parentElement!;
    expect(content.tagName).toBe("SPAN");
    expect(content.className.split(/\s+/)).toContain("min-w-0");
  });
});

describe("More", () => {
  it("tells a reader which disclosure this is when the label repeats", () => {
    // /developers shows "Response fields" under every endpoint; tabbing
    // through, a screen reader heard the same name a dozen times. The
    // context is in the accessible name and off the screen.
    render(
      <More label="Response fields" context="of GET /senators">
        <p>fields</p>
      </More>
    );
    const summary = screen.getByText("Response fields").closest("summary")!;
    expect(summary.textContent).toContain("Response fields of GET /senators");
    expect(screen.getByText("of GET /senators").className).toContain("sr-only");
  });
});

describe("AboutPage contents", () => {
  const chapter = (sections: number) => (
    <AboutPage href="/about/scores" eyebrow="e" title="t" lede={<p>lede</p>}>
      <Summary>
        <Point>short</Point>
      </Summary>
      {Array.from({ length: sections }, (_, i) => (
        <Section key={i} id={`s${i}`} title={`Section ${i}`}>
          <P>body</P>
        </Section>
      ))}
    </AboutPage>
  );

  it("lists a long chapter's sections after its summary, linked by their ids", () => {
    render(chapter(4));
    const nav = screen.getByRole("navigation", { name: "On this page" });
    const links = within(nav).getAllByRole("link");
    expect(links.map((a) => a.getAttribute("href"))).toEqual(["#s0", "#s1", "#s2", "#s3"]);
    expect(links[0]).toHaveTextContent("Section 0");
    // After the summary, before the first section.
    const summary = screen.getByRole("heading", { name: "The short version" });
    expect(summary.compareDocumentPosition(nav) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    const first = screen.getByRole("heading", { name: "Section 0" });
    expect(nav.compareDocumentPosition(first) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("leaves a short chapter without one", () => {
    render(chapter(3));
    expect(screen.queryByRole("navigation", { name: "On this page" })).toBeNull();
  });
});
