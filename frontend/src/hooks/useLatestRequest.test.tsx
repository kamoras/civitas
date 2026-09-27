import { describe, expect, it } from "vitest";
import { act, renderHook, waitFor } from "@testing-library/react";
import { useLatestRequest } from "./useLatestRequest";

function deferred<T>() {
  let resolve: (value: T) => void = () => {};
  let reject: (reason: unknown) => void = () => {};
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

describe("useLatestRequest", () => {
  it("lets only the newest request land", async () => {
    const { result } = renderHook(() => useLatestRequest<string, string>("all", "Failed"));
    const first = deferred<string>();
    const second = deferred<string>();
    act(() => result.current.request("a", () => first.promise));
    act(() => result.current.request("b", () => second.promise));
    await act(async () => second.resolve("B"));
    await act(async () => first.resolve("A"));
    expect(result.current).toMatchObject({ data: "B", shown: "b", requested: "b", loading: false });
  });

  it("keeps the data on a failure and returns `requested` to what is shown", async () => {
    const { result } = renderHook(() => useLatestRequest<string, string>("all", "Failed to load"));
    await act(async () => result.current.request("all", async () => "ALL"));
    act(() => result.current.request("nay", () => Promise.reject(new Error("boom"))));
    expect(result.current.requested).toBe("nay");
    await waitFor(() => expect(result.current.error).toBe("boom"));
    expect(result.current).toMatchObject({ data: "ALL", shown: "all", requested: "all", loading: false });
  });

  it("reports a fetcher that throws synchronously as an error", async () => {
    const { result } = renderHook(() => useLatestRequest<string, null>(null, "Failed to load"));
    act(() =>
      result.current.request(null, () => {
        throw "not an Error";
      })
    );
    await waitFor(() => expect(result.current.error).toBe("Failed to load"));
  });
});
