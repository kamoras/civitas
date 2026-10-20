"use client";

import { membersHref } from "@/lib/routes";
import { useId } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useYourState } from "@/hooks/useYourState";
import { INLINE_LINK } from "@/lib/controlStyles";
import { STATE_NAMES } from "@/lib/stateCodes";
import { stateBallotHref } from "@/lib/elections";

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
}: {
  label: string;
  /** Where a pick goes: the state's members, or its ballot page. */
  to: "members" | "ballot";
}) {
  const hrefFor = (state: string) =>
    to === "ballot" ? stateBallotHref(state) : membersHref(state);
  const router = useRouter();
  const id = useId();
  const { state } = useYourState();
  const name = state ? (STATE_NAMES[state] ?? state) : "";
  return (
    <div className="mt-3">
      {state && (
        <Link href={hrefFor(state)} className={`inline-flex min-h-11 items-center ${INLINE_LINK}`}>
          {name}&apos;s {to === "ballot" ? "ballot" : "members"} &rarr;
        </Link>
      )}
      <label htmlFor={id} className="block text-sm text-ink-lo">
        {state ? `Another state` : label}
      </label>
      <select
        id={id}
        defaultValue=""
        onChange={(e) => e.target.value && router.push(hrefFor(e.target.value))}
        className="mt-1 min-h-11 w-full border border-ink-hi bg-surface px-2 text-base text-ink-hi"
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
