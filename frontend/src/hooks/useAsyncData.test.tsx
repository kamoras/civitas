import { describe, expect, it, vi } from "vitest";
import { act, render, screen, waitFor } from "@testing-library/react";
import { useAsyncData } from "./useAsyncData";

function Probe({ k, fetcher }: { k: string; fetcher: (() => Promise<string>) | null }) {
  const { data, error, loading } = useAsyncData(k, fetcher);
  return (
    <div>
      <span data-testid="state">{loading ? "loading" : (error ?? data ?? "idle")}</span>
    </div>
  );
}

describe("useAsyncData", () => {
  it("reports loading without ever setting it, then the data", async () => {
    render(<Probe k="a" fetcher={() => Promise.resolve("value")} />);
    expect(screen.getByTestId("state")).toHaveTextContent("loading");
    await waitFor(() => expect(screen.getByTestId("state")).toHaveTextContent("value"));
  });

  it("surfaces an error message rather than hanging on loading", async () => {
    render(<Probe k="a" fetcher={() => Promise.reject(new Error("boom"))} />);
    await waitFor(() => expect(screen.getByTestId("state")).toHaveTextContent("boom"));
  });

  it("handles a rejection that is not an Error", async () => {
    render(<Probe k="a" fetcher={() => Promise.reject("just a string")} />);
    await waitFor(() => expect(screen.getByTestId("state")).toHaveTextContent("Request failed"));
  });

  it("is idle, not loading, when disabled", () => {
    render(<Probe k="a" fetcher={null} />);
    expect(screen.getByTestId("state")).toHaveTextContent("idle");
  });

  it("refetches when the key changes and never shows the old key's data", async () => {
    const fetcher = vi.fn((v: string) => () => Promise.resolve(v));
    const { rerender } = render(<Probe k="a" fetcher={fetcher("first")} />);
    await waitFor(() => expect(screen.getByTestId("state")).toHaveTextContent("first"));

    rerender(<Probe k="b" fetcher={fetcher("second")} />);
    // The critical assertion: the moment the key changes, the previous key's
    // data is gone. Storing `loading` separately is what lets stale data show
    // under a new set of filters.
    expect(screen.getByTestId("state")).toHaveTextContent("loading");
    await waitFor(() => expect(screen.getByTestId("state")).toHaveTextContent("second"));
  });

  it("does not refetch when only the fetcher identity changes", async () => {
    const calls = vi.fn();
    const make = () => () => {
      calls();
      return Promise.resolve("v");
    };
    const { rerender } = render(<Probe k="same" fetcher={make()} />);
    await waitFor(() => expect(screen.getByTestId("state")).toHaveTextContent("v"));
    rerender(<Probe k="same" fetcher={make()} />);
    rerender(<Probe k="same" fetcher={make()} />);
    // Callers write the fetcher inline, so it is a new function every render.
    // Depending on it would refetch forever.
    expect(calls).toHaveBeenCalledTimes(1);
  });

  it("ignores a response that resolves after the key moved on", async () => {
    let resolveFirst: (v: string) => void = () => {};
    const slow = () => new Promise<string>((r) => (resolveFirst = r));
    const { rerender } = render(<Probe k="a" fetcher={slow} />);
    rerender(<Probe k="b" fetcher={() => Promise.resolve("second")} />);
    await waitFor(() => expect(screen.getByTestId("state")).toHaveTextContent("second"));

    await act(async () => {
      resolveFirst("first");
    });
    expect(screen.getByTestId("state")).toHaveTextContent("second");
  });
});

describe("useAsyncData caching", () => {
  it("does not refetch when disabled and re-enabled with the same key", async () => {
    const calls = vi.fn();
    const fetcher = () => {
      calls();
      return Promise.resolve("v");
    };
    const { rerender } = render(<Probe k="president" fetcher={fetcher} />);
    await waitFor(() => expect(screen.getByTestId("state")).toHaveTextContent("v"));

    // Switch away (disabled) and back — this is a tab change, not new data.
    rerender(<Probe k="president" fetcher={null} />);
    rerender(<Probe k="president" fetcher={fetcher} />);
    await waitFor(() => expect(screen.getByTestId("state")).toHaveTextContent("v"));

    expect(calls).toHaveBeenCalledTimes(1);
  });

  it("still refetches when the key changes after a disable", async () => {
    const calls = vi.fn();
    const make = (v: string) => () => {
      calls();
      return Promise.resolve(v);
    };
    const { rerender } = render(<Probe k="page-1" fetcher={make("one")} />);
    await waitFor(() => expect(screen.getByTestId("state")).toHaveTextContent("one"));

    rerender(<Probe k="page-1" fetcher={null} />);
    rerender(<Probe k="page-2" fetcher={make("two")} />);
    await waitFor(() => expect(screen.getByTestId("state")).toHaveTextContent("two"));

    expect(calls).toHaveBeenCalledTimes(2);
  });
});

function RetryProbe({ fetcher }: { fetcher: () => Promise<string> }) {
  const { data, error, loading, retry } = useAsyncData("k", fetcher);
  return (
    <div>
      <span data-testid="state">{loading ? "loading" : (error ?? data ?? "idle")}</span>
      <button onClick={retry}>retry</button>
    </div>
  );
}

describe("useAsyncData retry", () => {
  it("re-runs a failed request and can succeed on the second attempt", async () => {
    let attempt = 0;
    const fetcher = () => {
      attempt += 1;
      return attempt === 1 ? Promise.reject(new Error("offline")) : Promise.resolve("recovered");
    };
    render(<RetryProbe fetcher={fetcher} />);
    await waitFor(() => expect(screen.getByTestId("state")).toHaveTextContent("offline"));

    act(() => {
      screen.getByText("retry").click();
    });
    await waitFor(() => expect(screen.getByTestId("state")).toHaveTextContent("recovered"));
  });
});
