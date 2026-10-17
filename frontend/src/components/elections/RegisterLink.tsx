import type { RegisterLink as Link } from "@/types/election";
import { safeHref } from "@/lib/formatting";

/** The words for a register link: the state's own page by its publisher,
 *  or vote.gov. */
export function registerWhere(link: Link): string {
  return link.statePage ? (link.publisher ?? "the state election office") : "vote.gov";
}

/** A sentence for a link the build script couldn't read past a bot wall,
 *  worded as the voter lookup's is (AGENTS.md section 7). */
export const UNCONFIRMED = "This site blocks automated checks, so we can't confirm it's up.";

/**
 * Where to register to vote: the state election office's page, checked by
 * the calendar's build script (backend scripts/build_election_calendar.py),
 * else vote.gov. Opens in a new tab; nothing about the visitor is sent.
 */
export default function RegisterLink({
  link,
  label = "Register",
  className = "",
  showNote = true,
}: {
  link: Link;
  label?: string;
  className?: string;
  /** Say when the link couldn't be confirmed (off in tight lists, where
   *  the label carries it). */
  showNote?: boolean;
}) {
  const href = safeHref(link.url);
  if (!href) return null;
  const where = registerWhere(link);
  return (
    <>
      <a
        href={href}
        target="_blank"
        rel="noopener noreferrer"
        className={`inline-flex min-h-6 items-center text-signal-cyan underline decoration-signal-cyan/40 underline-offset-4 hover:text-phos ${className}`}
        aria-label={`${label}: ${where}${link.confirmed ? "" : ", unconfirmed"} (opens in new tab)`}
      >
        {label} ↗
      </a>
      {showNote && !link.confirmed && (
        <span className="ml-1 text-xs text-ink-lo">{UNCONFIRMED}</span>
      )}
    </>
  );
}
