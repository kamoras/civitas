"use client";

/**
 * The interactive half of the full-story page: the same enrichment and civic
 * actions an issue carries in the Action Center.
 *
 * Split out as a client component because the surrounding page is a server
 * component but this needs the reader's saved state (localStorage) to offer the
 * right "contact your representatives" fallback, plus the pulse vote, the
 * action log and the share controls.
 */

import { useUserState } from "@/hooks/useUserState";
import StancePulse from "@/components/action/StancePulse";
import { LogActionButton } from "@/components/action/CivicTracker";
import ShareButtons from "@/components/action/ShareButtons";
import {
  RepresentativeContacts,
  TrackLegislation,
  OfficialLegislation,
  RelatedDocuments,
  SourceList,
} from "@/components/action/IssueEnrichment";
import type { ActionIssue } from "@/types/action";

export default function IssueActions({
  issue,
  today,
  shareUrl,
}: {
  issue: ActionIssue;
  /** Resolved on the server so the open/closed state of a comment period is
   *  identical in the prerendered HTML and after hydration. */
  today: string;
  shareUrl: string;
}) {
  const [userState] = useUserState();

  const hasWhatYouCanDo =
    (issue.relatedSenators?.length ?? 0) > 0 ||
    issue.actions.some((a) => a.type === "track_legislation" && a.url) ||
    userState !== null;

  return (
    <>
      {hasWhatYouCanDo && (
        <section className="mb-10">
          <h2 className="text-xs text-ink-min mb-4 tracking-widest">WHAT YOU CAN DO</h2>
          <RepresentativeContacts issue={issue} userState={userState} />
          <TrackLegislation issue={issue} />
        </section>
      )}

      {/* Each shared block brings its own heading — deliberately no extra <h2>
          wrapper here, or the page reads "RELATED LEGISLATION / Official
          Legislation" twice over for the same list. */}
      <section className="mb-10">
        <OfficialLegislation issue={issue} />
        <RelatedDocuments issue={issue} today={today} className="mb-6" />
        <SourceList
          issue={issue}
          className="flex items-center gap-2 flex-wrap pt-4 border-t border-white/[0.07]"
        />
      </section>

      <section className="mb-10">
        <StancePulse
          issueId={issue.id}
          initialConcerned={issue.concernedCount || 0}
          initialNotPriority={issue.notPriorityCount || 0}
        />
        <div className="mt-3 flex justify-end">
          <LogActionButton issueTitle={issue.title} />
        </div>
        <ShareButtons issue={issue} shareUrl={shareUrl} />
      </section>
    </>
  );
}
