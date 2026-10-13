import type { GeneralRunoff, OtherPrimary, TopPrimary } from "@/types/election";

/** "December 12" from "2026-12-12", read as a calendar date (no clock). */
function dayWords(iso: string): string {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, d)).toLocaleDateString("en-US", {
    month: "long",
    day: "numeric",
    timeZone: "UTC",
  });
}

function officeWords(offices: GeneralRunoff["offices"]): string {
  if (offices === "*") return "every contest on this ballot";
  const names = offices.map((o) =>
    o === "S" ? "the U.S. Senate contest" : "the U.S. House contests"
  );
  return names.join(" and ");
}

/**
 * Where November does not settle a contest by plurality: Georgia's
 * majority rule, and Louisiana's 2026 House contests, which are an
 * all-party open primary on general-election day. Without it a ballot
 * listing five Republicans for one Louisiana seat reads as a mistake, and
 * a Georgia leader on election night reads as the winner. The backend
 * reads each rule from a cited legal fact (election_rules.json); the page
 * only words it.
 */
export default function GeneralRunoffNotice({ runoffs }: { runoffs?: GeneralRunoff[] | null }) {
  if (!runoffs?.length) return null;
  return (
    <div className="mb-6 flex flex-col gap-2" data-testid="general-runoff">
      {runoffs.map((r) => (
        <p
          key={`${String(r.offices)}-${r.runoffDate}`}
          className="font-mono text-xs leading-relaxed text-ink-lo"
        >
          {r.openPrimary
            ? `In ${officeWords(r.offices)}, November 3 is an all-party primary: every candidate of every party is on one ballot. A candidate who wins a majority takes the seat; otherwise the top two meet in a runoff on ${dayWords(r.runoffDate)}.`
            : `In ${officeWords(r.offices)}, winning takes a majority of the votes. If no one gets one, the top two meet in a runoff on ${dayWords(r.runoffDate)}.`}
        </p>
      ))}
    </div>
  );
}

const COUNT_WORDS: Record<number, string> = { 2: "two", 3: "three", 4: "four" };

/**
 * An all-party primary (California's and Washington's top two, Alaska's
 * top four): without it, a California district with two Democrats
 * (or two Republicans) for one seat reads as a listing error. Read from a
 * cited legal fact (election_rules.json); the page only words it.
 */
export function TopPrimaryNotice({
  rules,
  stateName,
}: {
  rules?: TopPrimary[] | null;
  stateName: string;
}) {
  if (!rules?.length) return null;
  return (
    <div className="mb-6 flex flex-col gap-2" data-testid="top-primary">
      {rules.map((r) => {
        const n = COUNT_WORDS[r.advance] ?? String(r.advance);
        const scope = r.offices === "*" ? "" : `For ${officeWords(r.offices)}, `;
        return (
          <p
            key={`${String(r.offices)}-${r.advance}`}
            className="font-mono text-xs leading-relaxed text-ink-lo"
          >
            {`${scope}${stateName} held a top-${n} primary. Every candidate, of every party, ran on one primary ballot. The ${n} with the most votes went on to November, whatever their party. So a November contest can have more than one candidate from the same party.`}
          </p>
        );
      })}
    </div>
  );
}

/**
 * Ranked-choice voting (Maine, Alaska), said before election day: it
 * changes how the ballot is marked, not only how it is counted. The
 * results pages say the counting half from election night (StateResults).
 */
export function RankedChoiceNotice({
  rankedChoice,
  stateName,
}: {
  rankedChoice?: boolean;
  stateName: string;
}) {
  if (!rankedChoice) return null;
  return (
    <p className="mb-6 font-mono text-xs leading-relaxed text-ink-lo" data-testid="ranked-choice">
      {`${stateName} decides its U.S. Senate and House contests by ranked-choice voting. You can rank the candidates in order of preference; ranking more than one is optional. If no one wins a majority of first choices, the last-place candidate is dropped and those ballots go to their next choice, round by round, until one candidate has a majority. The state counts those rounds after election night.`}
    </p>
  );
}

/** "U.S. HOUSE 1, 2, 6, 7" -- which contests an OtherPrimary date is for,
 * in the header's own capitals. */
export function otherPrimaryLabel(o: OtherPrimary): string {
  if (o.offices === "*") return "ALL CONTESTS";
  const office = o.offices.map((x) => (x === "S" ? "U.S. SENATE" : "U.S. HOUSE")).join(" & ");
  return o.districts?.length ? `${office} ${o.districts.join(", ")}` : office;
}
