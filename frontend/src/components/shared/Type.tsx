import type { HTMLAttributes, ReactNode } from "react";
import { TYPE } from "@/lib/typeStyles";

/**
 * The type scale's roles as elements (lib/typeStyles.ts has the scale and
 * why). A heading's level is its place in the outline; its look is its
 * role, so each takes `as` where a page needs another level.
 */

type Props<T extends string> = HTMLAttributes<HTMLElement> & {
  as?: T;
  className?: string;
  children: ReactNode;
};

// The element: its level in the outline (the look is the role).
type Level = "h1" | "h2" | "h3" | "h4" | "p";

const join = (role: string, extra?: string) => (extra ? `${role} ${extra}` : role);

/** The page's h1. */
export function PageTitle({ as: Tag = "h1", className, ...rest }: Props<Level>) {
  return <Tag className={join(TYPE.title, className)} {...rest} />;
}

/** A part of the page: its h2. */
export function SectionHeading({ as: Tag = "h2", className, ...rest }: Props<Level>) {
  return <Tag className={join(TYPE.section, className)} {...rest} />;
}

/** A box's or a list item's title: its h3. */
export function BoxTitle({ as: Tag = "h3", className, ...rest }: Props<Level>) {
  return <Tag className={join(TYPE.boxTitle, className)} {...rest} />;
}

/** Where a figure or a section's data came from, and as of when. */
export function SourceLine({ className, ...rest }: Omit<Props<never>, "as">) {
  return <p className={join(TYPE.small, className)} {...rest} />;
}

/** A record's own number (H.R. 837, roll call 248), set in DM Mono. */
export function RecordId({ className, ...rest }: Omit<Props<never>, "as">) {
  return <span className={join(TYPE.id, className)} {...rest} />;
}
