"use client";

import Link from "next/link";
import type { RegistrationClosing } from "@/types/thisWeek";
import { useYourState } from "@/hooks/useYourState";
import { stateBallotHref } from "@/lib/elections";
import { STATE_NAMES } from "@/lib/stateCodes";
import RegisterLink from "@/components/elections/RegisterLink";

/**
 * The closing-soon strip's line for the state chosen in the header for this
 * tab (useYourState, AGENTS.md §8: read in the browser, never sent): its
 * deadline and register link first when it is closing soon, else that it
 * isn't among them, with its deadlines one link away. Nothing without a
 * chosen state.
 */
export default function YourStateClosing({ rows }: { rows: RegistrationClosing[] }) {
  const { state, ready } = useYourState();
  if (!ready || !state) return null;
  const row = rows.find((r) => r.state === state);
  const name = STATE_NAMES[state] ?? state;
  const href = `${stateBallotHref(state)}#voter-deadlines`;
  return (
    <p className="mt-2 border-l-2 border-phos/60 pl-2 text-sm text-ink">
      <span className="font-mono text-xs text-ink-min">Your state · </span>
      {row ? (
        <>
          <Link
            href={href}
            className="text-ink-hi underline decoration-white/30 underline-offset-4 hover:text-phos"
          >
            {name}
          </Link>
          : {row.sentence}
          {row.daysLeft === 0
            ? " (last day)"
            : ` (${row.daysLeft} day${row.daysLeft === 1 ? "" : "s"} left)`}
          {row.register && (
            <>
              {" "}
              <RegisterLink link={row.register} />
            </>
          )}
        </>
      ) : (
        <>
          {name} isn&apos;t among them.{" "}
          <Link
            href={href}
            className="text-signal-cyan underline decoration-signal-cyan/40 underline-offset-4 hover:text-phos"
          >
            {name}&apos;s deadlines
          </Link>
        </>
      )}
    </p>
  );
}
