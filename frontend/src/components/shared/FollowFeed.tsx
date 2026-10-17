import Link from "next/link";
import type { Feed } from "@/lib/site";

/**
 * "Follow <subject> without an account": the page's own feed (the one its
 * <head> advertises first, pageMetadata's `feed`), and /feeds for how to
 * follow one. A feed is pulled, so there is nothing to sign up for and no
 * list of followers (AGENTS.md §8).
 *
 * On a phone it reads "Follow · feeds", two 44px targets, with the rest of
 * each link's name kept for a screen reader: the full line was a 14px
 * two-link sentence in the first screen of every detail page (mobile spec P3).
 */
export default function FollowFeed({
  subject,
  feed,
  className = "",
}: {
  subject: string;
  feed: Feed;
  className?: string;
}) {
  return (
    <p className={`font-mono text-xs text-ink-lo ${className}`}>
      <a
        href={feed.url}
        type="application/atom+xml"
        className="inline-flex min-h-11 items-center text-signal-cyan underline underline-offset-2 hover:text-phos sm:inline sm:min-h-0"
      >
        Follow <span className="sr-only sm:not-sr-only">{subject} without an account</span>
      </a>{" "}
      ·{" "}
      <Link
        href="/feeds"
        className="inline-flex min-h-11 items-center underline underline-offset-2 hover:text-phos sm:inline sm:min-h-0"
      >
        <span className="sr-only sm:not-sr-only">about</span> feeds
      </Link>
    </p>
  );
}
