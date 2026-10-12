import Link from "next/link";
import type { Feed } from "@/lib/site";

/**
 * "Follow <subject> without an account": the page's own feed (the one its
 * <head> advertises first, pageMetadata's `feed`), and /feeds for how to
 * follow one. A feed is pulled, so there is nothing to sign up for and no
 * list of followers (AGENTS.md §8).
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
        className="text-signal-cyan underline underline-offset-2 hover:text-phos"
      >
        Follow {subject} without an account
      </a>{" "}
      ·{" "}
      <Link href="/feeds" className="underline underline-offset-2 hover:text-phos">
        about feeds
      </Link>
    </p>
  );
}
