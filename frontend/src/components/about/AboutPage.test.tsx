import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Item, List } from "./AboutPage";

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
