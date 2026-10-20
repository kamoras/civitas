"use client";

import Link from "next/link";
import type { SponsoredBill } from "@/types/senator";
import type { EffectivenessFacts, ScoreBreakdownDimension } from "@/types/scoreBreakdown";
import { useConfig } from "@/hooks/useConfig";
import ScoreColumn, { Block } from "./ScoreColumn";
import { count } from "./format";
import { billHref } from "@/lib/congress";

// Bills listed as furthest along; the drawer lists every one.
const BILLS_SHOWN = 4;

const TOOLTIP =
  "How far the member's bills and joint resolutions got this Congress, from Congress.gov bill status: introduced only, action in committee, past committee, passed the chamber, became law. Simple and concurrent resolutions are counted apart.";

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
  onMore,
}: {
  chamber: "senate" | "house";
  bills: SponsoredBill[];
  dimension: ScoreBreakdownDimension | undefined;
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
      title="Bills"
      shareId="legislative-effectiveness"
      tooltip={TOOLTIP}
      finding={dimension?.finding}
      more={{
        label: `All ${bills.length} sponsored bills and resolutions`,
        onClick: onMore,
      }}
    >
      {facts?.bills != null && dimension?.finding ? (
        // The finding states the bills; the stage counts are charted below.
        facts.resolutions > 0 && (
          <p className="text-sm leading-relaxed text-ink-lo">
            Besides these,{" "}
            {count(
              facts.resolutions,
              "simple or concurrent resolution",
              "simple or concurrent resolutions"
            )}
            .
          </p>
        )
      ) : facts?.bills != null ? (
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
                <span className="shrink-0 font-mono text-xs text-ink-min">
                  {(b.stage && config?.billStages[b.stage]?.name) || b.stage}
                </span>
              </li>
            ))}
          </ul>
        </Block>
      )}
    </ScoreColumn>
  );
}
