import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import RecordsBand, {
  RunStatusSquare,
  __resetStatusCache,
  describeRunState,
  formatRunTimestamp,
} from "./RecordsBand";
import type { PipelineLink, PipelineRunInfo, PipelineStatus } from "@/lib/api";

function run(overrides: Partial<PipelineRunInfo> = {}): PipelineRunInfo {
  return {
    id: 1284,
    startedAt: "2026-08-18T03:00:00Z",
    completedAt: "2026-08-18T03:07:00Z",
    status: "completed",
    currentPhase: null,
    senatorsProcessed: 100,
    senatorsTotal: 100,
    senatorsFailed: 0,
    billsClassified: 0,
    llmCalls: 0,
    cacheHits: 0,
    cacheMisses: 0,
    elapsedSeconds: 420,
    errorMessage: null,
    ...overrides,
  };
}

const LABELS = ["Senate", "Supplementary", "House", "Stock trades", "Election"];

/** The five links' latest runs: every one completed unless overridden by label. */
function links(states: Partial<Record<string, PipelineLink["state"]>> = {}): PipelineLink[] {
  return LABELS.map((label, i) => {
    const state = states[label] ?? "completed";
    return {
      pipeline: label.toLowerCase().replace(" ", "_"),
      label,
      state,
      runId: 100 + i,
      startedAt: `2026-08-18T0${3 + i}:00:00`,
      completedAt: state === "running" ? null : `2026-08-18T0${3 + i}:26:00`,
    };
  });
}

const status = (o: Partial<PipelineStatus> = {}): PipelineStatus => {
  const l = o.links ?? links();
  const done = l.filter((x) => x.state === "completed");
  return {
    lastRun: run(),
    nextScheduled: null,
    isRunning: false,
    links: l,
    linksCompleted: done.length,
    filedAt: done.at(-1)?.completedAt ?? null,
    ...o,
  };
};

const fetchPipelineStatus = vi.hoisted(() => vi.fn());
vi.mock("@/lib/api", () => ({ fetchPipelineStatus }));

afterEach(() => {
  cleanup();
  __resetStatusCache();
  vi.clearAllMocks();
});

describe("formatRunTimestamp", () => {
  it("renders UTC at minute precision so the band never drifts by locale", () => {
    // The whole value of a "filed at" stamp is that two people comparing
    // notes see the same string; a locale-formatted one does not.
    expect(formatRunTimestamp("2026-08-18T03:07:42Z")).toBe("2026-08-18 03:07 UTC");
    // Single-digit months, days, hours and minutes are padded.
    expect(formatRunTimestamp("2026-01-05T04:09:00Z")).toBe("2026-01-05 04:09 UTC");
  });

  it("treats an offset-less timestamp as UTC — which is the shape the API sends", () => {
    // backend/app/time_utils.py returns a NAIVE utc datetime by design, so
    // Pydantic serialises completedAt with no Z and no offset. ECMA-262 parses
    // that date-time form as LOCAL time, so a plain `new Date` here would
    // shift the stamp by the viewer's offset and still label it "UTC". This
    // case is the one the endpoint actually produces; the Z-suffixed case
    // above never occurs in production.
    expect(formatRunTimestamp("2026-08-18T03:07:00")).toBe("2026-08-18 03:07 UTC");
  });

  it("returns null for an unparseable timestamp rather than 'NaN-NaN-NaN'", () => {
    expect(formatRunTimestamp("not a date")).toBeNull();
  });
});

describe("describeRunState", () => {
  it("reports COMPLETE only when every link's latest run finished", () => {
    expect(describeRunState(status()).label).toBe("Complete");
    expect(describeRunState(status()).complete).toBe(true);
  });

  it("names the one link that failed, and when, instead of the whole site", () => {
    const s = describeRunState(status({ links: links({ Senate: "failed" }) }));
    expect(s.label).toBe("Senate data run failed");
    expect(s.short).toBe("Senate data failed");
    expect(s.at).toBe("2026-08-18T03:26:00");
    expect(s.tone).toBe("text-signal-red");
  });

  it("names the first failed link and counts the rest, every name kept, no single time", () => {
    const s = describeRunState(
      status({ links: links({ Senate: "failed", House: "failed", "Stock trades": "failed" }) })
    );
    expect(s.label).toBe("Senate + 2 more data runs failed");
    expect(s.short).toBe("3 data runs failed");
    expect(s.everyName).toBe("Senate, House, Stock trades");
    expect(s.at).toBeNull();
  });

  it("ranks a failure over a partial run over a run in progress", () => {
    const all = links({ Senate: "running", House: "partial", Election: "failed" });
    expect(describeRunState(status({ links: all })).label).toBe("Election data run failed");
    expect(
      describeRunState(status({ links: links({ Senate: "running", House: "partial" }) })).label
    ).toBe("House data run partial");
    const running = describeRunState(status({ links: links({ House: "running" }) }));
    expect(running.label).toBe("House data run in progress");
    expect(running.at).toBeNull();
  });

  it("handles an unreachable endpoint and an empty history separately", () => {
    expect(describeRunState(null).label).toBe("Status unavailable");
    expect(describeRunState(status({ links: [] })).label).toBe("No run recorded");
  });

  it("never colours a non-complete state with the phosphor accent", () => {
    // Phosphor is the 'good' signal in this palette; using it for a failure
    // would make a broken pipeline read as a healthy one at a glance.
    for (const s of [
      null,
      status({ links: [] }),
      status({ links: links({ House: "running" }) }),
      status({ links: links({ House: "partial" }) }),
      status({ links: links({ Senate: "failed" }) }),
    ]) {
      expect(describeRunState(s).tone).not.toBe("text-phos");
    }
  });
});

describe("RecordsBand rendering", () => {
  it("names the failed link, says how many links filed, and links the explanation", async () => {
    fetchPipelineStatus.mockResolvedValue(status({ links: links({ Senate: "failed" }) }));
    render(<RecordsBand />);

    // jsdom applies no CSS, so the phone and desktop wordings both show here.
    const link = await screen.findByRole("link");
    expect(link).toHaveAttribute("href", "/about/data#nightly-runs");
    expect(link).toHaveTextContent("Senate data run failed");
    expect(link).toHaveTextContent("2026-08-18 03:26 UTC");
    expect(screen.getByText(/4 of 5 filed/)).toBeInTheDocument();
    expect(screen.getByText("4/5 filed")).toBeInTheDocument(); // the phone line
    // The filed stamp is the newest completion among the four that completed.
    expect(screen.getByText(/2026-08-18 07:26 UTC/)).toBeInTheDocument();
    expect(screen.queryByText(/Complete/)).not.toBeInTheDocument();
  });

  it("prints the FILED stamp and COMPLETE when every link completed", async () => {
    fetchPipelineStatus.mockResolvedValue(status());
    render(<RecordsBand />);

    expect(await screen.findByText(/Filed 2026-08-18 07:26 UTC/)).toBeInTheDocument();
    expect(screen.getByText("Complete")).toBeInTheDocument();
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
  });

  it("prints no FILED time when no link has completed", async () => {
    fetchPipelineStatus.mockResolvedValue(
      status({ links: links({ Senate: "failed" }).slice(0, 1) })
    );
    render(<RecordsBand />);

    expect(await screen.findByText(/0 of 1 filed/)).toBeInTheDocument();
    expect(screen.queryByText(/UTC.*UTC/)).not.toBeInTheDocument();
  });

  it("fetches once across remounts instead of on every navigation", async () => {
    // Navbar (and so this band) mounts per page, not in the root layout, and
    // /api/pipeline/status runs db.expire_all() and a query per link with no
    // Cache-Control — on a Raspberry Pi.
    fetchPipelineStatus.mockResolvedValue(status());

    const first = render(<RecordsBand />);
    await screen.findByText(/Filed/);
    first.unmount();
    render(<RecordsBand />);
    await screen.findByText(/Filed/);

    expect(fetchPipelineStatus).toHaveBeenCalledTimes(1);
  });

  it("shares one in-flight request between simultaneous mounts", async () => {
    fetchPipelineStatus.mockResolvedValue(status());
    render(
      <>
        <RecordsBand />
        <RecordsBand />
      </>
    );
    await screen.findAllByText(/Filed/);

    expect(fetchPipelineStatus).toHaveBeenCalledTimes(1);
  });

  it("says the status is unavailable rather than rendering an empty band", async () => {
    fetchPipelineStatus.mockResolvedValue(null);
    render(<RecordsBand />);

    expect(await screen.findByText(/Status unavailable/)).toBeInTheDocument();
  });
});

describe("RunStatusSquare (the band on a phone)", () => {
  it("is a 44px button named with the whole status, filled when every run completed", async () => {
    fetchPipelineStatus.mockResolvedValue(status());
    render(<RunStatusSquare />);
    const button = await screen.findByRole("button", {
      name: "Data status: Filed 2026-08-18 07:26 UTC, complete",
    });
    expect(button.className).toContain("min-h-11");
    expect(button.querySelector("[aria-hidden]")!.className).toContain("bg-phos");
  });

  it("is hollow when a run failed, and names it", async () => {
    fetchPipelineStatus.mockResolvedValue(status({ links: links({ Senate: "failed" }) }));
    render(<RunStatusSquare />);
    const button = await screen.findByRole("button", {
      name: "Data status: senate data run failed, 4 of 5 filed",
    });
    const square = button.querySelector("[aria-hidden]")!;
    expect(square.className).toContain("border-signal-red");
    expect(square.className).not.toMatch(/\bbg-/);
  });

  it("opens the status line on a tap and closes on Escape", async () => {
    fetchPipelineStatus.mockResolvedValue(status());
    render(<RunStatusSquare />);
    const button = await screen.findByRole("button", { name: /^Data status/ });
    const line = document.getElementById(button.getAttribute("aria-controls")!)!;
    expect(line.hidden).toBe(true);
    fireEvent.click(button);
    expect(button).toHaveAttribute("aria-expanded", "true");
    expect(line.hidden).toBe(false);
    expect(line).toHaveTextContent("Filed 2026-08-18 07:26 UTC");
    fireEvent.keyDown(document, { key: "Escape" });
    expect(line.hidden).toBe(true);
  });
});
