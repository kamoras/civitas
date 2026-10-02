import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import ComponentBars from "./ComponentBars";

describe("ComponentBars", () => {
  it("lists a component the scorer couldn't measure, without a bar or a score", () => {
    render(
      <ComponentBars
        components={[
          { label: "PAC dependency", score: 92.4, detail: "1% of contributions came from PACs" },
          { label: "Industry concentration", score: null, detail: "too little to measure its mix" },
        ]}
      />
    );
    expect(screen.getByText("92")).toBeInTheDocument();
    expect(screen.getByText("Industry concentration")).toBeInTheDocument();
    expect(screen.getByText("not measured")).toBeInTheDocument();
    expect(screen.getByText("n/a")).toBeInTheDocument();
  });

  it("renders nothing when no component was measured", () => {
    const { container } = render(
      <ComponentBars components={[{ label: "Industry concentration", score: null, detail: "x" }]} />
    );
    expect(container).toBeEmptyDOMElement();
  });
});
