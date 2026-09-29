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

function stageLabels(chamber: "senate" | "house"): string[] {
  return [
    "Introduced or referred",
    "Committee action",
    "Out of committee",
    `Passed the ${chamber === "house" ? "House" : "Senate"}`,
    "Became law",
  ];
}

function Lede({ byStage, chamber }: { byStage: number[]; chamber: "senate" | "house" }) {
  const total = byStage.reduce((a, b) => a + b, 0);
  if (total === 0) {
    return <p className="text-base leading-relaxed text-ink">No bills sponsored this Congress.</p>;
  }
  const [, committee, beyond, passed, law] = byStage;
  const chamberName = chamber === "house" ? "the House" : "the Senate";
  return (
    <p className="text-base leading-relaxed text-ink">
      Sponsored {count(total, "bill", "bills")} this Congress.{" "}
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
  const byStage = (dimension?.facts as EffectivenessFacts | undefined)?.billsByStage;
  const total = byStage?.reduce((a, b) => a + b, 0) ?? bills.length;
  const order = (stage: string | null | undefined) =>
    (stage && config?.billStages[stage]?.order) || 0;
  // Furthest first; only bills that got past automatic referral.
  const furthest = [...bills]
    .filter((b) => order(b.stage) > (config?.billStages.REFERRED?.order ?? 2))
    .sort((a, b) => order(b.stage) - order(a.stage))
    .slice(0, BILLS_SHOWN);
  const labels = stageLabels(chamber);

  return (
    <ScoreColumn
      title="Legislative Effectiveness"
      shareId="legislative-effectiveness"
      weight={weight}
      score={score}
      more={{ label: `All ${total} sponsored bills`, onClick: onMore }}
    >
      {byStage ? (
        <Lede byStage={byStage} chamber={chamber} />
      ) : (
        <p className="text-base text-ink-lo">No sponsored bills on record yet.</p>
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
