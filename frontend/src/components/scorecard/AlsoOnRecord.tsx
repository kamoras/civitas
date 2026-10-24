"use client";

import Link from "@/components/Link";
import BoxGrid from "@/components/shared/BoxGrid";
import { CHIP } from "@/components/shared/RecordBox";
import Holdings from "@/components/checker/Holdings";
import { countLobbiedBills } from "@/lib/lobbying";
import { asLabel } from "@/lib/formatting";
import { documentsHref } from "@/lib/routes";
import { SHARE_EXCLUDE_ATTR } from "@/lib/shareImage";
import type { Senator } from "@/types/senator";
import type { GovernmentDoc, GovernmentRecord, MemberBoxes } from "@/types/politicians";
import MemberBox from "./MemberBox";

export type RecordView = "trades" | "donorVotes" | "positions";

const DOC_TYPE_LABELS: Record<string, string> = {
  "Senate Floor Speech": "Floor speech",
  "House Floor Speech": "Floor speech",
  "Executive Order": "Exec order",
  Proclamation: "Proclamation",
  "Supreme Court Opinion": "Court opinion",
  "Presidential Memorandum": "Memo",
};
// Documents named in the box; the rest of the recent ones are behind More.
const DOCS_SHOWN = 3;

const OPEN = "min-h-11 text-left text-small text-ink underline underline-offset-2 hover:text-phos";

function DocRow({ doc }: { doc: GovernmentDoc }) {
  const typeLabel = DOC_TYPE_LABELS[doc.docType] ?? asLabel(doc.docType);
  return (
    <li className="flex items-start gap-3 text-small">
      <span className="w-24 shrink-0 font-id text-ink-min">{doc.date ?? "—"}</span>
      <span className="min-w-0 flex-1 [overflow-wrap:anywhere]">
        <span className="mr-2 text-ink-lo">[{typeLabel}]</span>
        {doc.url ? (
          <a
            href={doc.url}
            target="_blank"
            rel="noopener noreferrer"
            className="text-ink underline decoration-white/20 underline-offset-2 hover:text-phos"
          >
            {doc.title}
          </a>
        ) : (
          <span className="text-ink-lo">{doc.title}</span>
        )}
      </span>
    </li>
  );
}

/** What is on record about the member but part of no measure, as boxes:
 *  holdings, trades, positions, donor and vote links and the government
 *  record. Every count and line is the API's (member_boxes); a full list
 *  opens behind the box's More or in the drawer. */
export default function AlsoOnRecord({
  member,
  chamber,
  boxes,
  record,
  onOpen,
}: {
  member: Senator;
  chamber: "senate" | "house";
  boxes: MemberBoxes | null | undefined;
  record: GovernmentRecord;
  onOpen: (view: RecordView) => void;
}) {
  const overlaps = member.lobbyingMatches?.length ?? 0;
  const lobbied = countLobbiedBills(member.lobbyingMatches ?? []);
  const areas = member.partisanDepth?.policyBreakdown ?? [];
  const docs = record.recentDocs;

  return (
    <BoxGrid
      label="Also on record"
      labelId="also-on-record"
      dek="Context from filings and records, not compared with other members."
    >
      <Holdings memberId={member.id} filer={chamber} variant="box" box={boxes?.holdings} />

      <MemberBox
        id="trades"
        label="Trades"
        box={boxes?.trades}
        fallback={{ kicker: "Stock trades", title: "Stock trades" }}
        more={{
          label: "every trade",
          children: (
            <div {...{ [SHARE_EXCLUDE_ATTR]: "" }}>
              <button type="button" onClick={() => onOpen("trades")} className={OPEN}>
                Every trade, with its report &rarr;
              </button>
            </div>
          ),
        }}
      />

      {areas.length > 0 && (
        <MemberBox
          id="positions"
          label="Positions"
          box={boxes?.positions}
          about="Read from party-labeled votes."
          fallback={{ kicker: "Positions by policy area", title: "Positions by policy area" }}
          more={{
            label: "each area's lean",
            children: (
              <div className="flex flex-col gap-2">
                <ul className="flex flex-col gap-1 text-small text-ink">
                  {areas.map((a) => (
                    <li key={a.area}>
                      {a.mark ? `${a.mark.label}: ${a.mark.shown}, ${a.mark.context}` : a.area}
                    </li>
                  ))}
                </ul>
                <div {...{ [SHARE_EXCLUDE_ATTR]: "" }}>
                  <button type="button" onClick={() => onOpen("positions")} className={OPEN}>
                    Positions, area by area &rarr;
                  </button>
                </div>
              </div>
            ),
          }}
        >
          <ul className="flex flex-wrap gap-1.5" aria-label="Policy areas">
            {areas.map((a) => (
              <li key={a.area} className="border border-white/30 px-2 py-0.5 text-small text-ink">
                {a.mark?.label ?? asLabel(a.area)}
              </li>
            ))}
          </ul>
        </MemberBox>
      )}

      <MemberBox
        id="donor-votes"
        label="Donor links"
        box={null}
        fallback={{
          kicker: "Donor and vote links",
          title:
            overlaps === 0
              ? "None found in tracked votes"
              : `${overlaps} donor-vote overlap${overlaps !== 1 ? "s" : ""}`,
          dek:
            lobbied > 0
              ? `${lobbied} bill${lobbied !== 1 ? "s" : ""} named in lobbying filings under donors' names. Donor industries beside votes cast; LDA lobbying filings.`
              : "Donor industries beside votes cast; LDA lobbying filings.",
        }}
        more={
          overlaps > 0
            ? {
                label: "every link",
                children: (
                  <div {...{ [SHARE_EXCLUDE_ATTR]: "" }}>
                    <button type="button" onClick={() => onOpen("donorVotes")} className={OPEN}>
                      Donor and vote links &rarr;
                    </button>
                  </div>
                ),
              }
            : null
        }
      />

      <MemberBox
        id="government-record"
        label="Record"
        box={boxes?.record}
        fallback={{ kicker: "Government record", title: `${record.totalDocs} documents` }}
        more={
          docs.length > DOCS_SHOWN || record.totalDocs > docs.length
            ? {
                label: "more documents",
                children: (
                  <div className="flex flex-col gap-2">
                    <ul className="flex flex-col gap-2">
                      {docs.slice(DOCS_SHOWN).map((d) => (
                        <DocRow key={d.id} doc={d} />
                      ))}
                    </ul>
                    {record.totalDocs > docs.length && (
                      <Link href={documentsHref({ politician_id: member.id })} className={CHIP}>
                        View all {record.totalDocs} documents &rarr;
                      </Link>
                    )}
                  </div>
                ),
              }
            : null
        }
      >
        {docs.length > 0 && (
          <ul className="flex flex-col gap-2">
            {docs.slice(0, DOCS_SHOWN).map((d) => (
              <DocRow key={d.id} doc={d} />
            ))}
          </ul>
        )}
      </MemberBox>
    </BoxGrid>
  );
}
