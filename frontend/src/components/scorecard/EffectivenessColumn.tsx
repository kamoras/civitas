"use client";

import Link from "next/link";
import type { SponsoredBill } from "@/types/senator";
import type { EffectivenessFacts, ScoreBreakdownDimension } from "@/types/scoreBreakdown";
import { useConfig } from "@/hooks/useConfig";
import ComponentBars from "./ComponentBars";
import ScoreColumn, { Block } from "./ScoreColumn";
import { count } from "./format";
import { billHref } from "@/lib/congress";

// Bills listed as furthest along; the drawer lists every one.
const BILLS_SHOWN = 4;

const TOOLTIP =
  "Whether the member's legislation goes anywhere, following Volden and Wiseman's Legislative Effectiveness Score. 60% is the member's bills, each credited at every stage it reached (a law counts for far more than an introduction), against the median sponsor of the same majority or minority status in this chamber; simple and concurrent resolutions count a fifth as much as bills. 25% is how central the member is in the chamber's cosponsorship network, pulled toward 50 for under six years in office. 15% is the share of cosponsors on the member's own bills who come from the other party, against the median member of the same party. Without cosponsorship data it is 70% bills and 30% network.";

function stageLabels(chamber: "senate" | "house"): string[] {
  return [
    "Introduced or referred",
    "Committee action",
    "Out of committee",
    `Passed the ${chamber === "house" ? "House" : "Senate"}`,
    "Became law",
  ];
}

/** The bill counts in a sentence. Bills and joint resolutions only, the
 *  ones the score's own sentence counts: a simple resolution the chamber
 *  agreed to (electing a member to a committee) is not a bill that passed. */
function Lede({ facts, chamber }: { facts: EffectivenessFacts; chamber: "senate" | "house" }) {
  const { billsByStage, bills, resolutions } = facts;
  const besides =
    resolutions > 0
      ? ` (and ${count(resolutions, "simple or concurrent resolution", "simple or concurrent resolutions")})`
      : "";
  if (bills === 0) {
    return (
      <p className="text-base leading-relaxed text-ink">
        No bills sponsored this Congress{besides}.
      </p>
    );
  }
  const [, committee, beyond, passed, law] = billsByStage;
  const chamberName = chamber === "house" ? "the House" : "the Senate";
  return (
    <p className="text-base leading-relaxed text-ink">
      Sponsored {count(bills, "bill", "bills")} this Congress{besides}.{" "}
      {passed > 0 ? `${passed} passed ${chamberName}` : `None has passed ${chamberName}`}
      {committee + beyond > 0 ? `, ${committee + beyond} got committee action` : ""};{" "}
      {law === 0 ? "none has" : `${law} ${law === 1 ? "has" : "have"}`} become law.
    </p>
  );
}

export default function EffectivenessColumn({
  chamber,
  bills,
  dimension,
  score,
  weight,
  onMore,
}: {
  chamber: "senate" | "house";
  bills: SponsoredBill[];
  dimension: ScoreBreakdownDimension | undefined;
  score: number;
  weight?: number;
  onMore: () => void;
}) {
  const config = useConfig();
  const facts = dimension?.facts as EffectivenessFacts | undefined;
  const byStage = facts?.billsByStage;
  const total = facts?.bills ?? 0;
  const order = (stage: string | null | undefined) =>
    (stage && config?.billStages[stage]?.order) || 0;
  const isBill = (type: string | undefined) =>
    !!type && !!config?.substantiveBillTypes?.includes(type.toUpperCase());
  // Furthest first; only bills (not resolutions, which the counts above
  // leave out too) that got past automatic referral.
  const furthest = [...bills]
    .filter((b) => isBill(b.billType))
    .filter((b) => order(b.stage) > (config?.billStages.REFERRED?.order ?? 2))
    .sort((a, b) => order(b.stage) - order(a.stage))
    .slice(0, BILLS_SHOWN);
  const labels = stageLabels(chamber);

  return (
    <ScoreColumn
      title="Legislative Effectiveness"
      shareId="legislative-effectiveness"
      tooltip={TOOLTIP}
      weight={weight}
      score={score}
      more={{
        label: `All ${bills.length} sponsored bills and resolutions`,
        onClick: onMore,
      }}
    >
      {facts?.bills != null ? (
        <Lede facts={facts} chamber={chamber} />
      ) : (
        !facts && <p className="text-base text-ink-lo">No sponsored bills on record yet.</p>
      )}

      {byStage && total > 0 && (
        <Block label="Bills by furthest stage reached">
          <ul className="flex flex-col gap-2">
            {byStage.map((n, i) => (
              <li
                key={labels[i]}
                className="grid grid-cols-[minmax(0,10rem)_minmax(0,1fr)_2rem] items-center gap-2.5 text-sm"
              >
                <span className="text-ink-lo">{labels[i]}</span>
                <span className="h-1.5 bg-white/[0.07]" aria-hidden="true">
                  <span
                    className="block h-full bg-ink"
                    style={{ width: `${(n / total) * 100}%` }}
                  />
                </span>
                <span className="text-right font-mono text-ink-hi">{n}</span>
              </li>
            ))}
          </ul>
        </Block>
      )}

      {furthest.length > 0 && (
        <Block label="Furthest along">
          <ul className="flex flex-col gap-1.5">
            {furthest.map((b) => (
              <li key={b.billId} className="flex items-baseline justify-between gap-3 text-sm">
                <Link
                  href={billHref(b.billId, b.congress)}
                  className="line-clamp-2 min-w-0 text-ink underline decoration-white/15 underline-offset-2 hover:text-phos"
                  title={b.title}
                >
                  {b.title}
                </Link>
                <span className="shrink-0 font-mono text-xs uppercase text-ink-min">
                  {(b.stage && config?.billStages[b.stage]?.name) || b.stage}
                </span>
              </li>
            ))}
          </ul>
        </Block>
      )}

      {dimension && <ComponentBars components={dimension.components} />}
    </ScoreColumn>
  );
}
