import type { ConstituentApproval as Approval } from "@/types/senator";
import type { MemberBox as Box } from "@/types/politicians";
import ShareSectionButton from "@/components/share/ShareSectionButton";
import { SHARE_EXCLUDE_ATTR } from "@/lib/shareImage";
import MemberBox from "./MemberBox";

const GROUP_LABELS = { D: "Democrats", R: "Republicans", I: "Independents & others" } as const;

const METHOD =
  "From the Cooperative Election Study, a national survey that asks each respondent whether they approve of the job their own House member and senators are doing. Split by the respondent's own party, as a share of those who gave an opinion. A House district has about a hundred respondents with an opinion, a few dozen per party, and the same member's figure barely agrees from one survey to the next. Where a group is small, the figure is pulled toward what a typical member of the same party gets from that group, by an amount measured from how much members really differ; a figure that comes mostly from that typical level rather than this member's own respondents is marked \"mostly based on similar members\".";

/** A share as printed: the API's words, or from a backend older than them,
 *  the share rounded as the API rounds it. */
const shown = (g: Approval["byParty"][number]) =>
  g.approveShown ?? (g.approve == null ? null : `${Math.round(g.approve * 100)}%`);

/** Survey approval of the member among their own constituents, by the
 * constituent's party (backend services/constituent_survey.py), as one box:
 * the backend's headline for the member's own party, a bar per party, and
 * behind More the respondents, the survey and the method. */
export default function ApprovalBox({
  approval,
  box,
}: {
  approval: Approval | null | undefined;
  box: Box | null | undefined;
}) {
  if (!approval || approval.byParty.length === 0) return null;
  return (
    <MemberBox
      id="approval"
      label="Approval"
      box={box}
      fallback={{ kicker: "Approval at home", title: "Approval among their own constituents" }}
      more={{
        label: "respondents and the survey",
        children: (
          <div className="flex flex-col gap-3">
            <ul
              className="flex flex-col gap-1.5"
              aria-label="Approval among constituents, by party"
            >
              {approval.byParty.map((g) => (
                // Wraps at a large text size: the figure drops under the
                // group rather than running past the box (WCAG 1.4.10).
                <li
                  key={g.party}
                  className="flex flex-wrap items-baseline justify-between gap-x-3 text-small"
                >
                  <span className="text-ink-lo">{GROUP_LABELS[g.party]}</span>
                  <span className="ml-auto min-w-0 text-right text-ink-hi [overflow-wrap:anywhere]">
                    {g.approve === null ? "too few respondents to measure" : `${shown(g)} approve`}
                    <span className="block text-small text-ink-min">
                      {g.respondents} with an opinion
                      {g.approve !== null && g.ownWeight != null && g.ownWeight < 0.5
                        ? " · mostly based on similar members"
                        : ""}
                    </span>
                  </span>
                </li>
              ))}
            </ul>
            <p className="text-small leading-relaxed text-ink-min">
              {approval.survey}, rating &ldquo;{approval.surveyedAs}&rdquo;.
            </p>
            <p className="text-small leading-relaxed text-ink-lo">{METHOD}</p>
            <div {...{ [SHARE_EXCLUDE_ATTR]: "" }}>
              <ShareSectionButton label="Approval at home" />
            </div>
          </div>
        ),
      }}
    >
      {/* A bar per party; the figures beside them are the API's words. */}
      <ul className="flex flex-col gap-1.5">
        {approval.byParty.map((g) => (
          <li
            key={g.party}
            className="grid grid-cols-[minmax(0,9rem)_minmax(0,1fr)_3rem] items-center gap-2.5 text-small"
          >
            <span className="text-ink-lo">{GROUP_LABELS[g.party]}</span>
            <span className="h-1.5 bg-white/[0.07]" aria-hidden="true">
              <span
                className="block h-full bg-ink"
                style={{ width: `${(g.approve ?? 0) * 100}%` }}
              />
            </span>
            <span className="text-right tabular-nums text-ink-hi">{shown(g) ?? "n/a"}</span>
          </li>
        ))}
      </ul>
    </MemberBox>
  );
}
