"use client";

import Link from "next/link";
import { useYourState } from "@/hooks/useYourState";
import { STATE_NAMES } from "@/lib/stateCodes";

/**
 * "How your senators voted": a roll call's member list, where the state
 * chosen in the header for this tab (useYourState, AGENTS.md §8: read in the
 * browser, never sent) is listed first. Named for that state once it is
 * known; the server render and a reader with none say "your".
 */
export default function YourMembersLink({
  href,
  chamber,
  className,
}: {
  href: string;
  chamber: "senate" | "house";
  className: string;
}) {
  const { state, ready } = useYourState();
  const who = chamber === "senate" ? "senators" : "representatives";
  const name = ready && state ? STATE_NAMES[state] : null;
  return (
    <Link href={`${href}#members`} className={className}>
      {name ? `How ${name}'s ${who} voted` : `How your ${who} voted`}
    </Link>
  );
}
