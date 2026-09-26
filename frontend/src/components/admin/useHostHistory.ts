"use client";

import { useEffect, useRef, useState } from "react";
import { fetchAdminSystemStats, type HostStats } from "@/lib/api";

export interface HostSample {
  time: number;
  loadPct: number | null;
  memPct: number;
  tempC: number | null;
  rxRate: number | null;
  txRate: number | null;
}

const POLL_MS = 5000;
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
      let rxRate: number | null = null;
      let txRate: number | null = null;
      if (s.netRxBytes != null && s.netTxBytes != null) {
        const prev = prevNet.current;
        if (prev && now > prev.time) {
          const dt = (now - prev.time) / 1000;
          rxRate = Math.max(0, (s.netRxBytes - prev.rx) / dt);
          txRate = Math.max(0, (s.netTxBytes - prev.tx) / dt);
        }
        prevNet.current = { rx: s.netRxBytes, tx: s.netTxBytes, time: now };
      }
      setStats(s);
      setHistory((h) =>
        [
          ...h,
          {
            time: now,
            loadPct: s.loadAvg ? Math.round((s.loadAvg[0] / s.cpuCount) * 100) : null,
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
