import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import RecordsBand, {
  __resetStatusCache,
  describeRunState,
  formatRunTimestamp,
} from "./RecordsBand";
import type { PipelineRunInfo, PipelineStatus } from "@/lib/api";

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

const status = (o: Partial<PipelineStatus> = {}): PipelineStatus => ({
  lastRun: run(),
  nextScheduled: null,
  isRunning: false,
  ...o,
});

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

  it("pads single-digit months, days, hours and minutes", () => {
    expect(formatRunTimestamp("2026-01-05T04:09:00Z")).toBe("2026-01-05 04:09 UTC");
  });

  it("returns null for an unparseable timestamp rather than 'NaN-NaN-NaN'", () => {
    expect(formatRunTimestamp("not a date")).toBeNull();
  });
});

describe("describeRunState", () => {
  it("reports COMPLETE only for a run that actually finished", () => {
    expect(describeRunState(status()).label).toBe("COMPLETE");
  });

  it("does not dress a failed run as fresh data", () => {
    // A stale record has to be visible rather than assumed — that is the
    // entire reason this band exists.
    expect(describeRunState(status({ lastRun: run({ status: "failed" }) })).label).toBe(
      "LAST RUN FAILED"
    );
    expect(describeRunState(status({ lastRun: run({ errorMessage: "boom" }) })).label).toBe(
      "LAST RUN FAILED"
    );
  });

  it("distinguishes a run still going from one that never finished", () => {
    expect(describeRunState(status({ isRunning: true })).label).toBe("RUN IN PROGRESS");
    expect(describeRunState(status({ lastRun: run({ completedAt: null }) })).label).toBe(
      "RUN INCOMPLETE"
    );
  });

  it("handles an unreachable endpoint and an empty history separately", () => {
    expect(describeRunState(null).label).toBe("STATUS UNAVAILABLE");
    expect(describeRunState(status({ lastRun: null })).label).toBe("NO RUN RECORDED");
  });

  it("never colours a non-complete state with the phosphor accent", () => {
    // Phosphor is the 'good' signal in this palette; using it for a failure
    // would make a broken pipeline read as a healthy one at a glance.
    for (const s of [
      null,
      status({ isRunning: true }),
      status({ lastRun: null }),
      status({ lastRun: run({ status: "failed" }) }),
      status({ lastRun: run({ completedAt: null }) }),
    ]) {
      expect(describeRunState(s).tone).not.toBe("text-phos");
    }
  });
});

describe("RecordsBand rendering", () => {
  it("does not print a FILED stamp beside a failed run", async () => {
    // A completedAt survives on a failed run, so printing it unconditionally
    // would claim the record is current as of a run that wrote nothing —
    // exactly the misreading this band exists to prevent.
    fetchPipelineStatus.mockResolvedValue(status({ lastRun: run({ status: "failed" }) }));
    render(<RecordsBand />);

    expect(await screen.findByText(/LAST RUN FAILED/)).toBeInTheDocument();
    expect(screen.queryByText(/FILED/)).not.toBeInTheDocument();
  });

  it("prints the FILED stamp for a run that actually completed", async () => {
    fetchPipelineStatus.mockResolvedValue(status());
    render(<RecordsBand />);

    expect(await screen.findByText(/FILED 2026-08-18 03:07 UTC/)).toBeInTheDocument();
  });

  it("fetches once across remounts instead of on every navigation", async () => {
    // Navbar (and so this band) mounts per page, not in the root layout, and
    // /api/pipeline/status runs db.expire_all() plus two queries with no
    // Cache-Control — on a Raspberry Pi.
    fetchPipelineStatus.mockResolvedValue(status());

    const first = render(<RecordsBand />);
    await screen.findByText(/FILED/);
    first.unmount();
    render(<RecordsBand />);
    await screen.findByText(/FILED/);

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
    await screen.findAllByText(/FILED/);

    expect(fetchPipelineStatus).toHaveBeenCalledTimes(1);
  });

  it("says the status is unavailable rather than rendering an empty band", async () => {
    fetchPipelineStatus.mockResolvedValue(null);
    render(<RecordsBand />);

    expect(await screen.findByText(/STATUS UNAVAILABLE/)).toBeInTheDocument();
  });
});
