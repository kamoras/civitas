import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ActionPage from "./page";
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
