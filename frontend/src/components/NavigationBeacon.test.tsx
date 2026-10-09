import { render } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

let pathname = "/";
vi.mock("next/navigation", () => ({ usePathname: () => pathname }));

// The module remembers the last page, so each test gets a fresh copy.
async function freshBeacon() {
  vi.resetModules();
  return (await import("./NavigationBeacon")).default;
}

describe("NavigationBeacon", () => {
  const sendBeacon = vi.fn<(url: string) => boolean>(() => true);

  beforeEach(() => {
    sendBeacon.mockClear();
    Object.defineProperty(navigator, "sendBeacon", { value: sendBeacon, configurable: true });
    pathname = "/";
  });

  it("counts the page it was opened on and each navigation after it", async () => {
    const Beacon = await freshBeacon();
    const { rerender } = render(<Beacon />);
    expect(sendBeacon).toHaveBeenCalledTimes(1);
    expect(sendBeacon.mock.calls[0][0]).toMatch(/\/track-visit\?path=%2F$/);

    pathname = "/issue/abc123";
    rerender(<Beacon />);
    expect(sendBeacon).toHaveBeenCalledTimes(2);
    expect(sendBeacon.mock.calls[1][0]).toMatch(/\/track-visit\?path=%2Fissue%2Fabc123$/);

    rerender(<Beacon />); // same page again: not a navigation
    expect(sendBeacon).toHaveBeenCalledTimes(2);
  });

  it("does not count a remount of the page it has already seen", async () => {
    const Beacon = await freshBeacon();
    render(<Beacon />).unmount();
    render(<Beacon />);
    expect(sendBeacon).toHaveBeenCalledTimes(1);
  });

  it("does not count the admin dashboard", async () => {
    pathname = "/admin";
    const Beacon = await freshBeacon();
    const { rerender } = render(<Beacon />);
    pathname = "/admin/usage";
    rerender(<Beacon />);
    expect(sendBeacon).not.toHaveBeenCalled();
  });
});
