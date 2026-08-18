import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { render, screen, act, cleanup, fireEvent } from "@testing-library/react";
import { useSessionToken, __resetSessionTokenCache } from "./useSessionToken";

const KEY = "test-token";

function Probe() {
  const { token, ready, signIn, signOut } = useSessionToken(KEY);
  return (
    <>
      <span data-testid="state">
        {ready ? "ready" : "pending"}:{token ?? "none"}
      </span>
      <button onClick={() => signIn("fresh")}>sign in</button>
      <button onClick={() => signIn("shared")}>sign in shared</button>
      <button onClick={() => signOut()}>sign out</button>
    </>
  );
}

const state = () => screen.getByTestId("state").textContent;

describe("useSessionToken", () => {
  beforeEach(() => {
    window.sessionStorage.clear();
    __resetSessionTokenCache();
  });

  afterEach(() => {
    cleanup();
    __resetSessionTokenCache();
    window.sessionStorage.clear();
  });

  it("picks up a token already in storage", () => {
    window.sessionStorage.setItem(KEY, "abc123");
    render(<Probe />);
    expect(state()).toBe("ready:abc123");
  });

  it("reports ready with no token when storage is empty", () => {
    render(<Probe />);
    expect(state()).toBe("ready:none");
  });

  it("persists a sign-in and exposes it immediately", () => {
    render(<Probe />);
    fireEvent.click(screen.getByText("sign in"));
    expect(state()).toBe("ready:fresh");
    expect(window.sessionStorage.getItem(KEY)).toBe("fresh");
  });

  it("clears storage on sign-out", () => {
    window.sessionStorage.setItem(KEY, "abc123");
    render(<Probe />);
    fireEvent.click(screen.getByText("sign out"));
    expect(state()).toBe("ready:none");
    expect(window.sessionStorage.getItem(KEY)).toBeNull();
  });

  it("keeps two readers of the same key in step", () => {
    render(
      <>
        <Probe />
        <Probe />
      </>
    );
    fireEvent.click(screen.getAllByText("sign in shared")[0]);
    for (const node of screen.getAllByTestId("state")) {
      expect(node.textContent).toBe("ready:shared");
    }
  });

  it("signs out when another tab clears the key", () => {
    window.sessionStorage.setItem(KEY, "abc123");
    render(<Probe />);
    act(() => {
      window.sessionStorage.removeItem(KEY);
      window.dispatchEvent(new StorageEvent("storage", { key: KEY }));
    });
    expect(state()).toBe("ready:none");
  });

  it("ignores storage events for unrelated keys", () => {
    window.sessionStorage.setItem(KEY, "abc123");
    render(<Probe />);
    act(() => {
      window.dispatchEvent(new StorageEvent("storage", { key: "something-else" }));
    });
    expect(state()).toBe("ready:abc123");
  });
});
