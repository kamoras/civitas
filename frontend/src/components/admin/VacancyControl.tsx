"use client";

import { useState } from "react";
import TerminalTitlebar from "@/components/TerminalTitlebar";
import { setPoliticianVacancy } from "@/lib/api";

// --- Seat Vacancy Control ---
const VACANCY_REASONS = ["deceased", "resigned", "expelled"] as const;

export function VacancyControl({ token }: { token: string }) {
  const [politicianId, setPoliticianId] = useState("");
  const [action, setAction] = useState<"vacate" | "restore">("vacate");
  const [reason, setReason] = useState<(typeof VACANCY_REASONS)[number]>("deceased");
  const [leftOfficeDate, setLeftOfficeDate] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [result, setResult] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const submit = async () => {
    if (!politicianId.trim()) return;
    setSubmitting(true);
    setResult(null);
    setError(null);
    try {
      const res = await setPoliticianVacancy(
        token,
        politicianId.trim(),
        action === "restore",
        action === "vacate" ? reason : undefined,
        action === "vacate" && leftOfficeDate ? leftOfficeDate : undefined
      );
      setResult(
        action === "vacate"
          ? `${res.name}'s seat marked vacant (${res.vacancyReason}).`
          : `${res.name}'s seat restored to current.`
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="panel">
      <TerminalTitlebar title="Seat vacancies" />
      <div className="p-4 space-y-3">
        <p className="text-ink-min text-xs font-mono">
          Marks a senator/representative&apos;s seat vacant (or restores it) without deleting their
          historical data. No automated detection — this is manual only.
        </p>
        <div className="flex flex-wrap items-end gap-2">
          <div className="flex flex-col gap-1">
            <label className="text-ink-lo text-xs font-mono tracking-wider">POLITICIAN ID</label>
            <input
              value={politicianId}
              onChange={(e) => setPoliticianId(e.target.value)}
              placeholder="e.g. lindsey-graham"
              className="bg-surface border border-white/[0.07] text-ink-hi text-xs font-mono px-2 py-1.5 w-48 focus:outline-none focus:border-phos/40"
            />
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-ink-lo text-xs font-mono tracking-wider">ACTION</label>
            <select
              value={action}
              onChange={(e) => setAction(e.target.value as "vacate" | "restore")}
              className="bg-surface border border-white/[0.07] text-ink-hi text-xs font-mono px-2 py-1.5 focus:outline-none focus:border-phos/40"
            >
              <option value="vacate">Mark vacant</option>
              <option value="restore">Restore to current</option>
            </select>
          </div>
          {action === "vacate" && (
            <>
              <div className="flex flex-col gap-1">
                <label className="text-ink-lo text-xs font-mono tracking-wider">REASON</label>
                <select
                  value={reason}
                  onChange={(e) => setReason(e.target.value as (typeof VACANCY_REASONS)[number])}
                  className="bg-surface border border-white/[0.07] text-ink-hi text-xs font-mono px-2 py-1.5 focus:outline-none focus:border-phos/40"
                >
                  {VACANCY_REASONS.map((r) => (
                    <option key={r} value={r}>
                      {r}
                    </option>
                  ))}
                </select>
              </div>
              <div className="flex flex-col gap-1">
                <label className="text-ink-lo text-xs font-mono tracking-wider">LEFT OFFICE</label>
                <input
                  type="date"
                  value={leftOfficeDate}
                  onChange={(e) => setLeftOfficeDate(e.target.value)}
                  className="bg-surface border border-white/[0.07] text-ink-hi text-xs font-mono px-2 py-1.5 focus:outline-none focus:border-phos/40"
                />
              </div>
            </>
          )}
          <button
            onClick={submit}
            disabled={submitting || !politicianId.trim()}
            className="font-mono text-xs text-signal-cyan border border-white/15 px-3 py-1.5 hover:bg-signal-cyan/10 transition-colors disabled:opacity-40"
          >
            {submitting ? "SUBMITTING..." : "SUBMIT"}
          </button>
        </div>
        {result && <p className="text-ink-hi text-xs font-mono">{result}</p>}
        {error && <p className="text-signal-magenta text-xs font-mono">{error}</p>}
      </div>
    </div>
  );
}
