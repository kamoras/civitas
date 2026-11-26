"use client";

import { membersHref } from "@/lib/routes";
import { useId } from "react";
import Link from "@/components/Link";
import { useRouter } from "next/navigation";
import { useYourState } from "@/hooks/useYourState";
import { INLINE_LINK } from "@/lib/controlStyles";
import { STATE_NAMES } from "@/lib/stateCodes";
import { stateBallotHref } from "@/lib/elections";
import { navigate } from "@/lib/navigate";

const STATES = Object.entries(STATE_NAMES).sort((a, b) => a[1].localeCompare(b[1]));

/**
 * A state picker that goes straight to that state's page. An explicit,
 * unremembered navigation choice (AGENTS.md section 8): nothing is kept.
 * With a state chosen in the header (this tab only), it leads with a link
 * straight to that state's page, named for it (WCAG 2.4.4), and the select
 * picks another.
 */
export default function StateJump({
  label,
  to,
  showYours = true,
}: {
  label: string;
  /** Off where the page already links the header's state itself
   *  (/elections' "Your state" line). */
  showYours?: boolean;
  /** Where a pick goes: the state's members, or its ballot page. */
  to: "members" | "ballot";
}) {
  const hrefFor = (state: string) =>
    to === "ballot" ? stateBallotHref(state) : membersHref(state);
  const router = useRouter();
  const id = useId();
  const { state } = useYourState();
  const name = state ? (STATE_NAMES[state] ?? state) : "";
  // One row where the width allows: the header state's link, then the
  // label beside its select (the homepage's task band, kept short so the
  // record below it starts on the first screen). Pushed to the foot of its
  // cell (mt-auto) at one fixed width from sm up, so the band's pickers line
  // up whatever each cell's heading wraps to.
  return (
    <div className="mt-auto flex flex-wrap items-center gap-x-3 gap-y-1">
      {state && showYours && (
        <Link href={hrefFor(state)} className={`inline-flex min-h-11 items-center ${INLINE_LINK}`}>
          {name}&apos;s {to === "ballot" ? "ballot" : "members"} &rarr;
        </Link>
      )}
      <label htmlFor={id} className="text-small text-ink-lo">
        {state ? `Another state` : label}
      </label>
      <select
        id={id}
        defaultValue=""
        onChange={(e) => e.target.value && navigate(router, hrefFor(e.target.value))}
        className="min-h-11 min-w-0 flex-1 border border-ink-hi bg-surface px-2 text-body text-ink-hi sm:w-60 sm:max-w-full sm:flex-none"
      >
        <option value="">Choose a state</option>
        {STATES.map(([code, name]) => (
          <option key={code} value={code}>
            {name}
          </option>
        ))}
      </select>
    </div>
  );
}
