"use client";

import { useState } from "react";
import Link from "next/link";
import { fetchDistrictForAddress } from "@/lib/api";

interface AddressLookupProps {
  /** The ballot page's own state (e.g. "GA") — a resolved address in a
   * DIFFERENT state points the visitor at that state's page instead of
   * silently doing nothing. */
  ballotState: string;
  /** Called with a real, resolved (ballotState-matching) district number —
   * the caller owns actually selecting it. Returns whether that district
   * was actually found among this state's races: Census's district data
   * can be on a different vintage than Civitas's own race data, so a
   * resolved district isn't guaranteed to match one we have. */
  onResolved: (district: number) => boolean;
}

type Status =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "wrong-state"; state: string }
  | { kind: "no-match" }
  | { kind: "district-not-found"; district: number };

/** Optional address -> district lookup, resolved server-side via the free
 * Census Bureau geocoder (GET /elections/geocode) — an alternative to the
 * manual dropdown, not a replacement for it. The address is sent once to
 * resolve a district and never stored anywhere, client or server (2026-08
 * — address collection scoped explicitly: ephemeral, resolve-only). */
export default function AddressLookup({ ballotState, onResolved }: AddressLookupProps) {
  const [address, setAddress] = useState("");
  const [status, setStatus] = useState<Status>({ kind: "idle" });

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const trimmed = address.trim();
    if (!trimmed) return;
    setStatus({ kind: "loading" });
    try {
      const result = await fetchDistrictForAddress(trimmed);
      if (result.state == null || result.district == null) {
        setStatus({ kind: "no-match" });
        return;
      }
      if (result.state !== ballotState) {
        setStatus({ kind: "wrong-state", state: result.state });
        return;
      }
      const found = onResolved(result.district);
      setStatus(
        found ? { kind: "idle" } : { kind: "district-not-found", district: result.district }
      );
    } catch {
      setStatus({ kind: "error", message: "Could not resolve that address right now." });
    }
  };

  return (
    <div className="mb-4">
      <form onSubmit={handleSubmit} className="flex flex-wrap gap-2">
        <input
          type="text"
          value={address}
          onChange={(e) => setAddress(e.target.value)}
          placeholder="Enter your address to find your district"
          aria-label="Your address"
          className="flex-1 min-w-[220px] bg-surface-base border border-white/15 text-ink-hi font-mono text-xs px-3 py-2 placeholder:text-ink-min"
        />
        <button
          type="submit"
          disabled={status.kind === "loading" || !address.trim()}
          className="font-mono text-xs px-3 py-2 border border-signal-cyan/40 text-signal-cyan hover:bg-signal-cyan/10 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
        >
          {status.kind === "loading" ? "LOOKING UP…" : "FIND MY DISTRICT"}
        </button>
      </form>
      <p className="text-xs text-ink-min mt-1.5">
        Resolved via the free U.S. Census Bureau geocoder — never stored, on this page or anywhere
        else.
      </p>
      {status.kind === "no-match" && (
        <p className="text-xs text-signal-amber mt-1.5">
          Couldn&apos;t match that address. Try including your city and ZIP code, or use the
          dropdown below.
        </p>
      )}
      {status.kind === "wrong-state" && (
        <p className="text-xs text-signal-amber mt-1.5">
          That address is in {status.state}, not {ballotState} —{" "}
          <Link href={`/elections/states/${status.state}`} className="text-signal-cyan underline">
            view {status.state}&apos;s ballot instead
          </Link>
          .
        </p>
      )}
      {status.kind === "district-not-found" && (
        <p className="text-xs text-signal-amber mt-1.5">
          Found district {status.district}, but it&apos;s not in our House data for {ballotState}{" "}
          yet — use the dropdown below.
        </p>
      )}
      {status.kind === "error" && (
        <p className="text-xs text-signal-red mt-1.5">{status.message}</p>
      )}
    </div>
  );
}
