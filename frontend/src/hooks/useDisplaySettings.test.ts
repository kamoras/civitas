import { beforeEach, describe, expect, it } from "vitest";
import {
  applySettings,
  DEFAULT_SETTINGS,
  parseSettings,
  type DisplaySettings,
} from "./useDisplaySettings";

describe("parseSettings", () => {
  it("returns defaults for missing storage", () => {
    expect(parseSettings(null)).toEqual(DEFAULT_SETTINGS);
  });

  it("returns defaults for malformed JSON rather than throwing", () => {
    expect(parseSettings("{not json")).toEqual(DEFAULT_SETTINGS);
  });

  it("round-trips a fully specified value", () => {
    const s: DisplaySettings = {
      theme: "light",
      legible: true,
      effects: false,
      textScale: 125,
    };
    expect(parseSettings(JSON.stringify(s))).toEqual(s);
  });

  it("rejects an unlisted text scale instead of applying it", () => {
    // Guards the CSS contract: --civitas-text-scale feeds a calc() on the root
    // font size, so an arbitrary stored number would scale the whole site.
    expect(parseSettings('{"textScale":900}').textScale).toBe(100);
    expect(parseSettings('{"textScale":"125"}').textScale).toBe(100);
  });

  it("treats an unknown theme as the terminal default", () => {
    expect(parseSettings('{"theme":"solarized"}').theme).toBe("dark");
  });

  it("keeps effects on unless explicitly disabled", () => {
    expect(parseSettings("{}").effects).toBe(true);
    expect(parseSettings('{"effects":false}').effects).toBe(false);
  });
});

describe("applySettings", () => {
  beforeEach(() => {
    const el = document.documentElement;
    delete el.dataset.theme;
    delete el.dataset.legible;
    delete el.dataset.effects;
    el.style.removeProperty("--civitas-text-scale");
  });

  it("writes the attributes the globals.css mode selectors key off", () => {
    applySettings({
      theme: "light",
      legible: true,
      effects: false,
      textScale: 150,
    });
    const el = document.documentElement;
    expect(el.dataset.theme).toBe("light");
    expect(el.dataset.legible).toBe("on");
    expect(el.dataset.effects).toBe("off");
    expect(el.style.getPropertyValue("--civitas-text-scale")).toBe("1.5");
  });

  it("emits explicit off/on values so no selector relies on the attribute's absence", () => {
    applySettings(DEFAULT_SETTINGS);
    const el = document.documentElement;
    expect(el.dataset.theme).toBe("dark");
    expect(el.dataset.legible).toBe("off");
    expect(el.dataset.effects).toBe("on");
    expect(el.style.getPropertyValue("--civitas-text-scale")).toBe("1");
  });
});
