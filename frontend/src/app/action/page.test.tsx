import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ActionPage from "./page";
import { ACTION_CENTER_HREF } from "@/lib/routes";
import { fetchActionIssues } from "@/lib/api";
import type { ActionIssue } from "@/types/action";

/* The tab bar writes every tab's URL through the History API, the issues
   tab included. A bare /action entry is what Next's client router cache
   later hands an in-app <Link href="/action"> back with (AGENTS.md,
   "Client-side URL state"), so the issues tab must name itself. */

/* useSearchParams follows the address bar the way Next's does: Next patches
   pushState/replaceState and listens for popstate, then re-renders every
   useSearchParams consumer with the new search. A mock that only read
   window.location on renders the page happened to do anyway could not see
   the page's own writes coming back — which is the trap under test. */
const nav = vi.hoisted(() => {
  const listeners = new Set<() => void>();
  const notify = () => listeners.forEach((l) => l());
  const subscribe = (l: () => void) => {
    listeners.add(l);
    window.addEventListener("popstate", l);
    return () => {
      listeners.delete(l);
      window.removeEventListener("popstate", l);
    };
  };
  return { notify, subscribe };
});
vi.mock("next/navigation", async () => {
  const React = await import("react");
  return {
    useSearchParams: () => {
      const search = React.useSyncExternalStore(nav.subscribe, () => window.location.search);
      return React.useMemo(() => new URLSearchParams(search), [search]);
    },
    usePathname: () => window.location.pathname,
    useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  };
});
vi.mock("@/lib/api", () => ({
  fetchActionIssues: vi.fn(() => new Promise(() => {})),
  fetchOpenComments: vi.fn(() => new Promise(() => {})),
}));
vi.mock("@/components/layout/Navbar", () => ({ default: () => <header /> }));
vi.mock("@/components/layout/Footer", () => ({ default: () => <footer /> }));
vi.mock("@/components/BackToTop", () => ({ default: () => null }));
vi.mock("@/components/action/CivicTracker", () => ({
  default: () => null,
  LogActionButton: () => null,
}));
vi.mock("@/components/action/StancePulse", () => ({ default: () => null }));
vi.mock("@/components/action/ShareButtons", () => ({ default: () => null }));
/* An in-app <Link> to this same route: a soft navigation, which in Next is a
   history push the page did not make. */
function softLink(href: string, label: string) {
  return (
    <a
      href={href}
      onClick={(e) => {
        e.preventDefault();
        window.history.pushState(null, "", href);
        nav.notify();
      }}
    >
      {label}
    </a>
  );
}
vi.mock("./TimelineTab", () => ({
  default: () => (
    <div>
      <p>timeline</p>
      {softLink("/action?date=2026-09-01", "Sept 1 entry")}
    </div>
  ),
}));
vi.mock("@/components/action/MyRepsTab", () => ({
  default: () => <div>{softLink("/action?issue=pub-b-latest", "Your rep in the news")}</div>,
}));
vi.mock("./MonitorsTab", () => ({ default: () => <p>monitors</p> }));

const DATES = ["2026-09-28", "2026-09-20", "2026-09-01"];
const LATEST = DATES[0];

function issueFor(date: string, slot: "a" | "b" | "c"): ActionIssue {
  const tag = date === LATEST ? "latest" : date;
  return {
    id: Number(date.replaceAll("-", "")) * 10 + slot.charCodeAt(0),
    publicId: `pub-${slot}-${tag}`,
    date,
    firstSurfaced: date,
    rank: slot.charCodeAt(0) - 96,
    title: `Issue ${slot} of ${date}`,
    summary: `Summary ${slot}`,
    facts: [],
    factSources: [],
    newFacts: [],
    actions: [],
    sourceUrls: [],
    sourceNames: [],
    policyAreas: [],
    relatedBills: [],
    relatedExploreDocs: [],
    relatedSenators: [],
    concernedCount: 0,
    notPriorityCount: 0,
    isTrending: false,
    status: "confirmed",
    sourceType: null,
  } as unknown as ActionIssue;
}

function serveIssues() {
  vi.mocked(fetchActionIssues).mockImplementation(((date?: string) => {
    const d = date ?? LATEST;
    return Promise.resolve({
      date: d,
      availableDates: DATES,
      generatedAt: `${d}T12:00:00Z`,
      issues: (["a", "b", "c"] as const).map((slot) => issueFor(d, slot)),
    });
  }) as unknown as typeof fetchActionIssues);
}

const scrolls = vi.fn();
beforeEach(() => {
  scrolls.mockClear();
  Element.prototype.scrollIntoView = scrolls;
  const push = History.prototype.pushState;
  const replace = History.prototype.replaceState;
  vi.spyOn(window.history, "pushState").mockImplementation(function (this: History, ...a) {
    push.apply(window.history, a);
    nav.notify();
  });
  vi.spyOn(window.history, "replaceState").mockImplementation(function (this: History, ...a) {
    replace.apply(window.history, a);
    nav.notify();
  });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.mocked(fetchActionIssues).mockImplementation(() => new Promise(() => {}));
  window.history.replaceState(null, "", "/");
});

describe("the Action Center tab bar", () => {
  it("names the issues tab in the URL when it is picked from another tab", async () => {
    window.history.replaceState(null, "", "/action?tab=timeline");
    render(<ActionPage />);
    await userEvent.click(await screen.findByRole("tab", { name: "ISSUES" }));
    expect(`${window.location.pathname}${window.location.search}`).toBe(ACTION_CENTER_HREF);
    expect(ACTION_CENTER_HREF).toBe("/action?tab=issues");
  });

  it("writes each other tab as ?tab=", async () => {
    window.history.replaceState(null, "", "/action?tab=issues");
    render(<ActionPage />);
    await userEvent.click(await screen.findByRole("tab", { name: "TIMELINE" }));
    expect(window.location.search).toBe("?tab=timeline");
  });
});

describe("arriving at a day or an issue from inside the Action Center", () => {
  /* In a production build, a <Link> to /action?date=… from the Timeline tab
     is a soft navigation: the page never remounts, so a ?date= latched only
     at mount showed today's issues under the new URL. */
  it("shows the day a Timeline entry links to", async () => {
    serveIssues();
    window.history.replaceState(null, "", "/action?tab=timeline");
    render(<ActionPage />);
    await userEvent.click(await screen.findByRole("link", { name: "Sept 1 entry" }));
    expect(await screen.findByText("Issue a of 2026-09-01")).toBeInTheDocument();
    expect(screen.queryByText(`Issue a of ${LATEST}`)).not.toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "ISSUES" })).toHaveAttribute("aria-selected", "true");
  });

  it("shows the linked day, not the day the page was opened on", async () => {
    serveIssues();
    window.history.replaceState(null, "", "/action?date=2026-09-20");
    render(<ActionPage />);
    expect(await screen.findByText("Issue a of 2026-09-20")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("tab", { name: "TIMELINE" }));
    await userEvent.click(await screen.findByRole("link", { name: "Sept 1 entry" }));
    expect(await screen.findByText("Issue a of 2026-09-01")).toBeInTheDocument();
  });

  it("opens and scrolls to the issue a My Reps link names", async () => {
    serveIssues();
    window.history.replaceState(null, "", "/action?tab=my-reps");
    render(<ActionPage />);
    await userEvent.click(await screen.findByRole("link", { name: "Your rep in the news" }));
    const card = await screen.findByRole("button", { name: new RegExp(`Issue b of ${LATEST}`) });
    expect(card).toHaveAttribute("aria-expanded", "true");
    await waitFor(() => expect(scrolls).toHaveBeenCalledTimes(1));
  });

  it("shows the latest day on coming back to ISSUES from another tab", async () => {
    serveIssues();
    window.history.replaceState(null, "", "/action?date=2026-09-20");
    render(<ActionPage />);
    await screen.findByText("Issue a of 2026-09-20");
    await userEvent.click(screen.getByRole("tab", { name: "TIMELINE" }));
    await userEvent.click(screen.getByRole("tab", { name: "ISSUES" }));
    expect(window.location.search).toBe("?tab=issues");
    expect(await screen.findByText(`Issue a of ${LATEST}`)).toBeInTheDocument();
  });

  /* AGENTS.md: params describing how the page was opened are latched. The
     page's own replaceState writes come straight back through
     useSearchParams and must not re-fire the arrival. */
  it("does not treat its own URL writes as an arrival", async () => {
    serveIssues();
    window.history.replaceState(null, "", `/action?issue=pub-b-latest`);
    render(<ActionPage />);
    await waitFor(() => expect(scrolls).toHaveBeenCalledTimes(1));
    const fetches = vi.mocked(fetchActionIssues).mock.calls.length;

    // Expanding another card writes ?issue=<it>; collapsing writes ?tab=issues;
    // paging a day writes ?date=.
    await userEvent.click(screen.getByRole("button", { name: new RegExp(`Issue c of ${LATEST}`) }));
    expect(window.location.search).toBe(`?issue=pub-c-${"latest"}`);
    await userEvent.click(screen.getByRole("button", { name: new RegExp(`Issue c of ${LATEST}`) }));
    expect(window.location.search).toBe("?tab=issues");
    await act(() => new Promise((r) => setTimeout(r, 150)));
    expect(scrolls).toHaveBeenCalledTimes(1);
    // No remount either: the same fetch serves the page.
    expect(vi.mocked(fetchActionIssues).mock.calls.length).toBe(fetches);

    await userEvent.click(screen.getByRole("button", { name: "Previous day" }));
    expect(window.location.search).toBe("?date=2026-09-20");
    expect(await screen.findByText("Issue a of 2026-09-20")).toBeInTheDocument();
    // One fetch for the new day — not a second from a remount on it.
    expect(
      vi.mocked(fetchActionIssues).mock.calls.filter(([d]) => d === "2026-09-20")
    ).toHaveLength(1);
    await act(() => new Promise((r) => setTimeout(r, 150)));
    expect(scrolls).toHaveBeenCalledTimes(1);
  });
});

describe("focus after Back/Forward", () => {
  async function popTo(url: string) {
    await act(async () => {
      window.history.replaceState(null, "", url);
      window.dispatchEvent(new PopStateEvent("popstate"));
    });
  }

  it("moves focus to the tab now showing when it was on the tab bar", async () => {
    window.history.replaceState(null, "", "/action?tab=issues");
    render(<ActionPage />);
    await userEvent.click(await screen.findByRole("tab", { name: "TIMELINE" }));
    expect(screen.getByRole("tab", { name: "TIMELINE" })).toHaveFocus();
    await popTo("/action?tab=issues");
    const issues = screen.getByRole("tab", { name: "ISSUES" });
    expect(issues).toHaveAttribute("tabindex", "0");
    expect(issues).toHaveFocus();
  });

  it("leaves focus alone when it was elsewhere on the page", async () => {
    window.history.replaceState(null, "", "/action?tab=timeline");
    render(<ActionPage />);
    const elsewhere = await screen.findByRole("link", { name: "Sept 1 entry" });
    elsewhere.focus();
    await popTo("/action?tab=monitors");
    expect(await screen.findByText("monitors")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "MONITORS" })).not.toHaveFocus();
  });
});

describe("a seat-flip issue's developing disclosure", () => {
  function serveCount(countOfficial: boolean) {
    const count = (slot: "a" | "b"): ActionIssue =>
      ({
        ...issueFor(LATEST, slot),
        title: `Democrat wins race ${slot} in the official count, taking a seat Republicans held`,
        status: "developing",
        sourceType: "election_results",
        countOfficial,
        countAsOf: `${LATEST}T02:00:00Z`,
      }) as ActionIssue;
    vi.mocked(fetchActionIssues).mockImplementation((() =>
      Promise.resolve({
        date: LATEST,
        availableDates: DATES,
        generatedAt: `${LATEST}T12:00:00Z`,
        issues: [count("a"), count("b")],
      })) as unknown as typeof fetchActionIssues);
  }

  async function disclosures() {
    window.history.replaceState(null, "", "/action?tab=issues");
    render(<ActionPage />);
    await userEvent.click(await screen.findByRole("button", { name: /race b/ }));
    return screen.getAllByText(/broader news coverage has not yet confirmed/);
  }

  it("says an official count is official, on the hero and on an expanded card", async () => {
    serveCount(true);
    const shown = await disclosures();
    expect(shown).toHaveLength(2);
    for (const p of shown) {
      expect(p.textContent).toMatch(/which the state lists as official/);
      expect(p.textContent).not.toMatch(/not final/);
    }
  });

  it("says a count that is not official is not final", async () => {
    serveCount(false);
    const shown = await disclosures();
    expect(shown).toHaveLength(2);
    for (const p of shown) expect(p.textContent).toMatch(/which is not final/);
  });
});
