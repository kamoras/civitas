"use client";

import Link from "next/link";
import { sendActionTap, type ActionTapKind } from "@/lib/api";

/**
 * A link on an issue whose use is counted: one more tap of `kind` under this
 * issue today, and nothing else (backend ActionTap). Client-only so the
 * server-rendered issue page can still render the rows around it.
 */
export default function TrackedLink({
  issuePublicId,
  kind,
  href,
  internal,
  className,
  ariaLabel,
  children,
}: {
  issuePublicId: string;
  kind: ActionTapKind;
  href: string;
  /** A same-site page (a client-side <Link>); otherwise a new tab, or the dialler for tel:. */
  internal: boolean;
  className?: string;
  ariaLabel?: string;
  children: React.ReactNode;
}) {
  const onClick = () => sendActionTap(issuePublicId, kind);
  if (internal) {
    return (
      <Link href={href} className={className} aria-label={ariaLabel} onClick={onClick}>
        {children}
      </Link>
    );
  }
  const tel = href.startsWith("tel:");
  return (
    <a
      href={href}
      className={className}
      aria-label={ariaLabel}
      onClick={onClick}
      {...(tel ? {} : { target: "_blank", rel: "noopener noreferrer" })}
    >
      {children}
    </a>
  );
}
