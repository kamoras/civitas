import type { ConstituentApproval as Approval } from "@/types/senator";
import MetricTooltip from "@/components/checker/MetricTooltip";
import { BlockLabel } from "./ScoreColumn";

const GROUP_LABELS = { D: "Democrats", R: "Republicans", I: "Independents & others" } as const;

const TOOLTIP =
  "From the Cooperative Election Study, a national survey that asks each respondent whether they approve of the job their own House member and senators are doing. Split by the respondent's own party, as a share of those who gave an opinion. A House district has about a hundred respondents with an opinion, a few dozen per party, and the same member's figure barely agrees from one survey to the next. Where a group is small, the figure is pulled toward what a typical member of the same party gets from that group, by an amount measured from how much members really differ; a figure that comes mostly from that typical level rather than this member's own respondents is marked \"mostly based on similar members\".";

/** Survey approval of the member among their own constituents, by the
 * constituent's party (backend services/constituent_survey.py). A block in
 * the votes column. */
export default function ConstituentApproval({
  approval,
}: {
  approval: Approval | null | undefined;
}) {
  if (!approval || approval.byParty.length === 0) return null;
  return (
    <div className="flex flex-col gap-2 border-t border-white/[0.08] pt-4">
      <BlockLabel words="Approval among their own constituents">
        <MetricTooltip text={TOOLTIP}>Approval among their own constituents</MetricTooltip>
      </BlockLabel>
      <ul className="flex flex-col gap-1.5" aria-label="Approval among constituents, by party">
        {approval.byParty.map((g) => (
          // Wraps at a large text size: the figure drops under the group
          // rather than running past the column (WCAG 1.4.10).
          <li
            key={g.party}
            className="flex flex-wrap items-baseline justify-between gap-x-3 text-sm"
          >
            <span className="text-ink-lo">{GROUP_LABELS[g.party]}</span>
            <span className="ml-auto min-w-0 text-right text-ink-hi [overflow-wrap:anywhere]">
              {g.approve === null
                ? "too few respondents to measure"
                : `${Math.round(g.approve * 100)}% approve`}
              <span className="block font-mono text-xs text-ink-min">
                {g.respondents} with an opinion
                {g.approve !== null && g.ownWeight != null && g.ownWeight < 0.5
                  ? " · mostly based on similar members"
                  : ""}
              </span>
            </span>
          </li>
        ))}
      </ul>
      <p className="text-xs leading-relaxed text-ink-min">
        {approval.survey}, rating &ldquo;{approval.surveyedAs}&rdquo;.
      </p>
    </div>
  );
}
