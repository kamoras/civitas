"use client";

import { useEffect, useRef, useState } from "react";
import { fetchAdminSystemStats, type HostStats } from "@/lib/api";

export interface HostSample {
  time: number;
  /** CPU utilisation (%) over the interval since the previous reading. */
  cpuPct: number | null;
  memPct: number;
  tempC: number | null;
  rxRate: number | null;
  txRate: number | null;
}

const POLL_MS = 5000;

/**
 * CPU utilisation (%) between two cumulative /proc/stat readings: the share
 * of ticks in the interval that were not idle. Null when either reading is
 * missing, or when no ticks elapsed (or the counters went backwards — a host
 * reboot between polls).
 */
export function cpuUtilisation(
  prev: { busy: number; total: number } | null,
  cur: { busy: number; total: number } | null
): number | null {
  if (!prev || !cur) return null;
  const dTotal = cur.total - prev.total;
  const dBusy = cur.busy - prev.busy;
  if (dTotal <= 0 || dBusy < 0) return null;
  return Math.min(100, (dBusy / dTotal) * 100);
}

/** 0.4 → "0.4%", 37.2 → "37%": small values keep a decimal so idle isn't "0%". */
export function formatPct(v: number): string {
  return v < 10 ? `${v.toFixed(1)}%` : `${Math.round(v)}%`;
}
/** Ten minutes at the poll rate — enough to see a spike and what followed it. */
const MAX_SAMPLES = 120;

/**
 * Polls host stats and keeps a rolling in-memory history of them.
 *
 * The backend exposes host stats only as a point-in-time reading, so "over
 * time" here means "since this dashboard was opened": nothing is persisted,
 * and a reload starts the history again. Owned by the dashboard shell rather
 * than the System tab so switching tabs doesn't throw the history away.
 */
export function useHostHistory(token: string, initial?: HostStats) {
  const [stats, setStats] = useState<HostStats | null>(initial ?? null);
  const [history, setHistory] = useState<HostSample[]>([]);
  const prevNet = useRef<{ rx: number; tx: number; time: number } | null>(null);
  const prevCpu = useRef<{ busy: number; total: number } | null>(null);

  useEffect(() => {
    let cancelled = false;
    const poll = async () => {
      let s: HostStats;
      try {
        s = await fetchAdminSystemStats(token);
      } catch {
        return;
      }
      if (cancelled) return;
      const now = Date.now();
      // This container's rate from its counters, plus the rate the API
      // containers recorded (a container sees only its own interfaces; a
      // missing part counts as nothing, never a jump). Either alone is
      // still a reading; neither is none.
      let ownRx: number | null = null;
      let ownTx: number | null = null;
      if (s.netRxBytes != null && s.netTxBytes != null) {
        const prev = prevNet.current;
        if (prev && now > prev.time) {
          const dt = (now - prev.time) / 1000;
          ownRx = Math.max(0, (s.netRxBytes - prev.rx) / dt);
          ownTx = Math.max(0, (s.netTxBytes - prev.tx) / dt);
        }
        prevNet.current = { rx: s.netRxBytes, tx: s.netTxBytes, time: now };
      }
      const apiRx = s.apiNetRxRate ?? null;
      const apiTx = s.apiNetTxRate ?? null;
      const rxRate = ownRx == null && apiRx == null ? null : (ownRx ?? 0) + (apiRx ?? 0);
      const txRate = ownTx == null && apiTx == null ? null : (ownTx ?? 0) + (apiTx ?? 0);
      const cpuNow =
        s.cpuBusyTicks != null && s.cpuTotalTicks != null
          ? { busy: s.cpuBusyTicks, total: s.cpuTotalTicks }
          : null;
      const cpuPct = cpuUtilisation(prevCpu.current, cpuNow);
      prevCpu.current = cpuNow;
      setStats(s);
      setHistory((h) =>
        [
          ...h,
          {
            time: now,
            cpuPct,
            memPct: s.memUsedPct,
            tempC: s.cpuTempC,
            rxRate,
            txRate,
          },
        ].slice(-MAX_SAMPLES)
      );
    };
    poll();
    const id = setInterval(poll, POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [token]);

  return { stats, history };
}
