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

    // Expanding another card writes ?issue=<it>; collapsing it goes back to
    // the deep-linked card, still open;
    // paging a day writes ?date=.
    await userEvent.click(screen.getByRole("button", { name: new RegExp(`Issue c of ${LATEST}`) }));
    expect(window.location.search).toBe(`?issue=pub-c-${"latest"}`);
    await userEvent.click(screen.getByRole("button", { name: new RegExp(`Issue c of ${LATEST}`) }));
    expect(window.location.search).toBe("?issue=pub-b-latest");
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

describe("a day with no issues left", () => {
  it("still has a pager, and doesn't call a past day empty for now", async () => {
    // Every issue on a day can move on (a re-matched issue is restamped to
    // the day that matched it), and the timeline still links to that day.
    vi.mocked(fetchActionIssues).mockImplementation(((date?: string) =>
      Promise.resolve({
        date: date ?? LATEST,
        availableDates: DATES,
        generatedAt: `${LATEST}T12:00:00Z`,
        issues: date === DATES[1] ? [] : (["a"] as const).map((slot) => issueFor(LATEST, slot)),
      })) as unknown as typeof fetchActionIssues);
    window.history.replaceState(null, "", `/action?tab=issues&date=${DATES[1]}`);
    render(<ActionPage />);
    expect(await screen.findByText("No issues are recorded for this day.")).toBeInTheDocument();
    expect(screen.queryByText(/Check back soon/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Previous day" })).not.toHaveAttribute(
      "aria-disabled",
      "true"
    );
    await userEvent.click(screen.getByRole("button", { name: "Next day" }));
    await waitFor(() =>
      expect(screen.queryByText("No issues are recorded for this day.")).not.toBeInTheDocument()
    );
    expect(vi.mocked(fetchActionIssues).mock.calls.map((c) => c[0])).toContain(undefined);
  });
});

describe("the day pager", () => {
  it("treats a malformed or impossible ?date= as no date", async () => {
    serveIssues();
    for (const bad of ["garbage", "2026-1-2", "2026-02-30", "0000-01-01"]) {
      window.history.replaceState(null, "", `/action?tab=issues&date=${bad}`);
      render(<ActionPage />);
      await screen.findByRole("button", { name: "Previous day" });
      expect(screen.queryByText("Invalid Date")).not.toBeInTheDocument();
      expect(vi.mocked(fetchActionIssues)).not.toHaveBeenCalledWith(bad);
      cleanup();
      vi.mocked(fetchActionIssues).mockClear();
    }
  });

  it("keeps keyboard focus on the button that turned the page", async () => {
    // The loading panel used to replace the pager, dropping focus to the
    // page body after every page turn.
    let release: () => void = () => {};
    serveIssues();
    window.history.replaceState(null, "", "/action?tab=issues");
    render(<ActionPage />);
    const prev = await screen.findByRole("button", { name: "Previous day" });
    const serve = vi.mocked(fetchActionIssues).getMockImplementation()!;
    vi.mocked(fetchActionIssues).mockImplementation(
      ((date?: string) =>
        new Promise((resolve) => {
          release = () => resolve(serve(date) as never);
        })) as unknown as typeof fetchActionIssues
    );
    prev.focus();
    await userEvent.keyboard("{Enter}");
    expect(await screen.findByText("SCANNING NEWS FEEDS...")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Previous day" })).toHaveFocus();
    await act(async () => release());
    expect(screen.getByRole("button", { name: "Previous day" })).toHaveFocus();
  });
});

describe("the day pager while a day loads", () => {
  it("waits for a deep-linked day before paging from it", async () => {
    // On a cold ?date= load there is no day list yet: NEXT sent the reader
    // to the latest day, not the next one.
    const pending: Array<() => void> = [];
    const release = () => pending.splice(0).forEach((r) => r());
    vi.mocked(fetchActionIssues).mockImplementation(
      ((date?: string) =>
        new Promise((resolve) => {
          pending.push(() =>
            resolve({
              date: date ?? LATEST,
              availableDates: DATES,
              generatedAt: `${LATEST}T12:00:00Z`,
              issues: [issueFor(date ?? LATEST, "a")],
            } as never)
          );
        })) as unknown as typeof fetchActionIssues
    );
    window.history.replaceState(null, "", `/action?tab=issues&date=${DATES[2]}`);
    render(<ActionPage />);
    const next = await screen.findByRole("button", { name: "Next day" });
    expect(next).toHaveAttribute("aria-disabled", "true");
    await userEvent.click(next);
    expect(window.location.search).toContain(`date=${DATES[2]}`);
    await act(async () => release());
    expect(await screen.findByText(`Issue a of ${DATES[2]}`)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Next day" })).not.toHaveAttribute(
      "aria-disabled",
      "true"
    );
  });

  it("keeps focus on LATEST once it has been used", async () => {
    serveIssues();
    window.history.replaceState(null, "", `/action?tab=issues&date=${DATES[1]}`);
    render(<ActionPage />);
    await screen.findByText(`Issue a of ${DATES[1]}`);
    const latest = screen.getByRole("button", { name: "Jump to present" });
    latest.focus();
    await userEvent.keyboard("{Enter}");
    expect(await screen.findByText(`Issue a of ${LATEST}`)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Jump to present" })).toHaveFocus();
    expect(screen.getByRole("button", { name: "Jump to present" })).toHaveAttribute(
      "aria-disabled",
      "true"
    );
  });

  it("doesn't scroll back to an issue link's card on paging back to its day", async () => {
    serveIssues();
    window.history.replaceState(null, "", "/action?issue=pub-b-latest");
    render(<ActionPage />);
    await waitFor(() => expect(scrolls).toHaveBeenCalledTimes(1));
    await userEvent.click(screen.getByRole("button", { name: "Previous day" }));
    expect(await screen.findByText(`Issue a of ${DATES[1]}`)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Next day" }));
    expect(await screen.findByText(`Issue a of ${LATEST}`)).toBeInTheDocument();
    await act(() => new Promise((r) => setTimeout(r, 150)));
    expect(scrolls).toHaveBeenCalledTimes(1);
  });
});

describe("the day pager after a failed load", () => {
  it("doesn't page from the previous day's list when a day fails to load", async () => {
    vi.mocked(fetchActionIssues).mockImplementation((() =>
      Promise.reject(new Error("502"))) as unknown as typeof fetchActionIssues);
    window.history.replaceState(null, "", `/action?tab=issues&date=${DATES[1]}`);
    render(<ActionPage />);
    expect(await screen.findByText("CONNECTION ERROR")).toBeInTheDocument();
    const next = screen.getByRole("button", { name: "Next day" });
    expect(next).toHaveAttribute("aria-disabled", "true");
    await userEvent.click(next);
    expect(window.location.search).toContain(`date=${DATES[1]}`);
  });

  it("keeps focus in the tab when RETRY is pressed", async () => {
    let fail = true;
    vi.mocked(fetchActionIssues).mockImplementation(((date?: string) =>
      fail
        ? Promise.reject(new Error("502"))
        : Promise.resolve({
            date: date ?? LATEST,
            availableDates: DATES,
            generatedAt: `${LATEST}T12:00:00Z`,
            issues: [issueFor(date ?? LATEST, "a")],
          })) as unknown as typeof fetchActionIssues);
    window.history.replaceState(null, "", "/action?tab=issues");
    render(<ActionPage />);
    const retry = await screen.findByRole("button", { name: "RETRY" });
    fail = false;
    retry.focus();
    await userEvent.keyboard("{Enter}");
    expect(await screen.findByText(`Issue a of ${LATEST}`)).toBeInTheDocument();
    expect(document.activeElement).not.toBe(document.body);
    expect(document.activeElement?.closest('[role="tabpanel"]')).not.toBeNull();
  });
});

describe("an issue expanded on an older day", () => {
  it("keeps the day in the URL beside the issue", async () => {
    serveIssues();
    window.history.replaceState(null, "", `/action?tab=issues&date=${DATES[1]}`);
    render(<ActionPage />);
    const card = await screen.findByRole("button", { name: new RegExp(`Issue b of ${DATES[1]}`) });
    await userEvent.click(card);
    expect(window.location.search).toBe(`?date=${DATES[1]}&issue=pub-b-${DATES[1]}`);
    await userEvent.click(card);
    expect(window.location.search).toBe(`?date=${DATES[1]}`);
  });
});

describe("the issue in the URL", () => {
  it("stays on the card still open when another is collapsed", async () => {
    serveIssues();
    window.history.replaceState(null, "", `/action?tab=issues&date=${DATES[1]}`);
    render(<ActionPage />);
    const b = await screen.findByRole("button", { name: new RegExp(`Issue b of ${DATES[1]}`) });
    const c = screen.getByRole("button", { name: new RegExp(`Issue c of ${DATES[1]}`) });
    await userEvent.click(b);
    await userEvent.click(c);
    expect(window.location.search).toBe(`?date=${DATES[1]}&issue=pub-c-${DATES[1]}`);
    await userEvent.click(b);
    expect(window.location.search).toBe(`?date=${DATES[1]}&issue=pub-c-${DATES[1]}`);
    await userEvent.click(c);
    expect(window.location.search).toBe(`?date=${DATES[1]}`);
  });

  it("stays on the earlier card when the newest open card is collapsed", async () => {
    serveIssues();
    window.history.replaceState(null, "", `/action?tab=issues&date=${DATES[1]}`);
    render(<ActionPage />);
    const b = await screen.findByRole("button", { name: new RegExp(`Issue b of ${DATES[1]}`) });
    const c = screen.getByRole("button", { name: new RegExp(`Issue c of ${DATES[1]}`) });
    await userEvent.click(b);
    await userEvent.click(c);
    await userEvent.click(c);
    expect(window.location.search).toBe(`?date=${DATES[1]}&issue=pub-b-${DATES[1]}`);
    await userEvent.click(b);
    expect(window.location.search).toBe(`?date=${DATES[1]}`);
  });

  it("scrolls a deep-linked card clear of the navbar and the sticky tab bar", async () => {
    serveIssues();
    window.history.replaceState(null, "", "/action?issue=pub-b-latest");
    render(<ActionPage />);
    const card = await screen.findByRole("button", { name: new RegExp(`Issue b of ${LATEST}`) });
    expect(card.closest("article")).toHaveClass("scroll-mt-[calc(var(--header-clearance)+3rem)]");
  });
});

describe("a site with a single day of issues", () => {
  it("keeps the pager (and focus) after LATEST", async () => {
    vi.mocked(fetchActionIssues).mockImplementation(((date?: string) =>
      Promise.resolve({
        date: date ?? LATEST,
        availableDates: [LATEST],
        generatedAt: `${LATEST}T12:00:00Z`,
        issues: [issueFor(LATEST, "a")],
      })) as unknown as typeof fetchActionIssues);
    window.history.replaceState(null, "", `/action?tab=issues&date=${LATEST}`);
    render(<ActionPage />);
    const latest = await screen.findByRole("button", { name: "Jump to present" });
    latest.focus();
    await userEvent.keyboard("{Enter}");
    expect(await screen.findByText(`Issue a of ${LATEST}`)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Jump to present" })).toHaveFocus();
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

  it("keeps focus on the tab Back showed when the click's own focus lands a frame late", async () => {
    // Clicking a tab focuses it on the next frame. A Back inside that frame
    // (or a slow frame) must not have focus pulled back to the tab it left.
    const frames: FrameRequestCallback[] = [];
    const raf = vi.spyOn(window, "requestAnimationFrame").mockImplementation((cb) => {
      frames.push(cb);
      return frames.length;
    });
    try {
      window.history.replaceState(null, "", "/action?tab=issues");
      render(<ActionPage />);
      await userEvent.click(await screen.findByRole("tab", { name: "TIMELINE" }));
      await popTo("/action?tab=issues");
      const issues = screen.getByRole("tab", { name: "ISSUES" });
      expect(issues).toHaveFocus();
      act(() => {
        for (const cb of frames.splice(0)) cb(performance.now());
      });
      expect(issues).toHaveFocus();
    } finally {
      raf.mockRestore();
    }
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
