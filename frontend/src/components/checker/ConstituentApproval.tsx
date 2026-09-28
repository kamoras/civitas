import type { ConstituentApproval as Approval } from "@/types/senator";
import MetricTooltip from "./MetricTooltip";

const GROUP_LABELS = { D: "Democrats", R: "Republicans", I: "Independents & others" } as const;

const TOOLTIP =
  "From the Cooperative Election Study, a national survey that asks each respondent whether they approve of the job their own House member and senators are doing. Split by the respondent's own party, as a share of those who gave an opinion. Not part of any score. Where a group is small, the figure is pulled toward what a typical member of the same party gets from that group, by an amount measured from how much members really differ; a figure that comes mostly from that typical level rather than this member's own respondents is marked \"mostly based on similar members\".";

/** Survey approval of the member among their own constituents, by the
 * constituent's party (backend services/constituent_survey.py). */
export default function ConstituentApproval({ approval }: { approval: Approval | null | undefined }) {
  if (!approval || approval.byParty.length === 0) return null;
  return (
    <div className="mt-2 border-t border-white/[0.07] pt-2 font-mono text-xs">
      <div className="text-ink-lo">
        <MetricTooltip text={TOOLTIP}>APPROVAL AMONG THEIR OWN CONSTITUENTS</MetricTooltip>
      </div>
      <ul className="mt-1 space-y-0.5" aria-label="Approval among constituents, by party">
        {approval.byParty.map((g) => (
          <li key={g.party} className="flex justify-between gap-2">
            <span className="text-ink-lo">{GROUP_LABELS[g.party]}</span>
            <span className="text-ink">
              {g.approve === null
                ? "too few respondents to measure"
                : `${Math.round(g.approve * 100)}% approve`}
              <span className="text-ink-min">
                {" "}
                · {g.respondents} with an opinion
                {g.approve !== null && g.ownWeight != null && g.ownWeight < 0.5
                  ? " · mostly based on similar members"
                  : ""}
              </span>
            </span>
          </li>
        ))}
      </ul>
      <div className="mt-1 text-ink-min">
        {approval.survey}, rating &ldquo;{approval.surveyedAs}&rdquo;. Informational, not scored.
      </div>
    </div>
  );
}
