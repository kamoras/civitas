"use client";

import { useEffect, useId, useRef, useState } from "react";
import { useYourState } from "@/hooks/useYourState";
import { stateFromPoint } from "@/lib/stateFromPoint";
import Link from "next/link";
import { STATE_CODES, STATE_NAMES } from "@/lib/stateCodes";
import { stateBallotHref } from "@/lib/elections";

// By name, as a reader looks for their state.
/** The code itself from the fixed list, or "": the confirmation builds hrefs
 *  from it, so nothing read from the page goes into a link unchecked. */
const knownCode = (code: string) => STATE_CODES.find((c) => c === code) ?? "";

const STATES_BY_NAME = [...STATE_CODES].sort((a, b) =>
  (STATE_NAMES[a] ?? a).localeCompare(STATE_NAMES[b] ?? b)
);

// The select's border is what shows it is a control (WCAG 1.4.11, 3:1):
// ink-min at 60% over the header is 4.1:1; white/20 was under 2:1.
/** How long the header's message box stays when nothing else moves it. */
const STATUS_SHOWN_MS = 8000;

const BUTTON =
  "inline-flex min-h-6 items-center font-mono text-xs text-ink-lo underline decoration-white/30 underline-offset-4 hover:text-ink-hi disabled:no-underline disabled:opacity-80";

/**
 * The reader's state, asked once, in the header, and used by every page
 * that needs one (the member directory, an issue's contact list, the
 * calendar, compare, the homepage's links). Held for this tab only
 * (useYourState); nothing about it reaches the server.
 *
 * "Use my location" never runs by itself. On a click it asks the browser
 * for a position and places it in a state here, against the site's own
 * state shapes (stateFromPoint). The coordinates are not sent, logged or
 * stored: only the two-letter answer is kept, as if picked from the list.
 * Refused or failed, it says so in the live region and the list stays.
 */
export default function YourStatePicker({
  size = "sm",
  onNavigate,
}: {
  size?: "sm" | "lg";
  /** Called when the reader follows one of the confirmation's links (the
   *  menu closes itself). */
  onNavigate?: () => void;
}) {
  const id = useId();
  const { state, setState } = useYourState();
  const [status, setStatus] = useState("");
  // The state just set here, which the message confirms with where to go
  // next (mobile spec P9): setting it used to say nothing and offer nothing.
  const [confirmed, setConfirmed] = useState("");
  const [locating, setLocating] = useState(false);
  const boxRef = useRef<HTMLDivElement>(null);
  const tall = size === "lg";

  // In the header the message is a small box over the bar below; it goes
  // when focus or a pointer moves elsewhere, or after a few seconds, so it
  // never covers a control a reader moves to (WCAG 2.4.11). It has been
  // announced by then: the live region speaks when the text is set.
  useEffect(() => {
    if (tall || !(status || confirmed) || locating) return;
    const box = boxRef.current;
    const away = (e: Event) => {
      if (!box?.contains(e.target as Node)) {
        setStatus("");
        setConfirmed("");
      }
    };
    // No timer while it offers links: they stay until the reader moves on
    // (WCAG 2.2.1).
    const timer = confirmed ? 0 : window.setTimeout(() => setStatus(""), STATUS_SHOWN_MS);
    document.addEventListener("focusin", away);
    document.addEventListener("pointerdown", away);
    return () => {
      window.clearTimeout(timer);
      document.removeEventListener("focusin", away);
      document.removeEventListener("pointerdown", away);
    };
  }, [tall, status, confirmed, locating]);
  const locate = () => {
    setConfirmed("");
    if (!("geolocation" in navigator)) {
      setStatus("This browser can't share a location. Pick your state from the list.");
      return;
    }
    setLocating(true);
    setStatus("Finding your state…");
    navigator.geolocation.getCurrentPosition(
      async ({ coords }) => {
        let code: string | null = null;
        try {
          code = await stateFromPoint(coords.longitude, coords.latitude);
        } catch {
          code = null;
        }
        setLocating(false);
        if (code) {
          setState(code);
          setStatus("");
          setConfirmed(knownCode(code));
        } else {
          setStatus("That location isn't in a U.S. state. Pick yours from the list.");
        }
      },
      () => {
        setLocating(false);
        setStatus("Location not available. Pick your state from the list.");
      },
      // A coarse fix is all a state needs, and an old one is fine.
      { enableHighAccuracy: false, maximumAge: 600_000, timeout: 15_000 }
    );
  };

  return (
    <div
      ref={boxRef}
      className={`flex flex-wrap items-center gap-x-3 gap-y-1 ${tall ? "" : "relative"}`}
    >
      <label htmlFor={id} className="font-mono text-xs text-ink-min">
        Your state
      </label>
      <select
        id={id}
        value={state}
        onChange={(e) => {
          setState(e.target.value);
          setStatus("");
          setConfirmed(knownCode(e.target.value));
        }}
        className={`${tall ? "min-h-11" : "min-h-6"} border border-ink-min/60 bg-surface-base px-1 font-mono text-xs text-ink`}
      >
        <option value="">Not set</option>
        {STATES_BY_NAME.map((code) => (
          <option key={code} value={code}>
            {STATE_NAMES[code] ?? code}
          </option>
        ))}
      </select>
      <button
        type="button"
        onClick={locate}
        disabled={locating}
        className={`${BUTTON} ${tall ? "min-h-11" : ""}`}
      >
        Use my location
      </button>
      {/* In the header's one-line band the message sits just under it
          rather than adding a line to the fixed header. */}
      <p
        role="status"
        aria-live="polite"
        // Always in the tree (an empty live region that appears later may
        // not be announced); boxed only while it has something to say.
        className={
          !(status || confirmed)
            ? "sr-only"
            : tall
              ? "basis-full font-mono text-xs text-ink-lo"
              : "absolute right-0 top-full z-20 mt-2 w-max max-w-xs border border-white/15 bg-surface-base px-3 py-1.5 font-mono text-xs text-ink-lo"
        }
      >
        {confirmed ? (
          <>
            {STATE_NAMES[confirmed] ?? confirmed} set for this tab.{" "}
            <span className="flex flex-wrap gap-x-4">
              <Link
                href={`/politicians?state=${confirmed}`}
                onClick={onNavigate}
                className="inline-flex min-h-11 items-center text-signal-cyan underline underline-offset-2 hover:text-phos"
              >
                {STATE_NAMES[confirmed] ?? confirmed}&apos;s members →
              </Link>
              <Link
                href={stateBallotHref(confirmed)}
                onClick={onNavigate}
                className="inline-flex min-h-11 items-center text-signal-cyan underline underline-offset-2 hover:text-phos"
              >
                {STATE_NAMES[confirmed] ?? confirmed}&apos;s ballot →
              </Link>
            </span>
          </>
        ) : (
          status
        )}
      </p>
    </div>
  );
}
