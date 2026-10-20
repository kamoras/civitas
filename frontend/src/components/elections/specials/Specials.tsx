import Link from "next/link";
import type { SeatVacancy, SpecialElection, SpecialsBoard } from "@/types/specials";
import { safeHref } from "@/lib/formatting";
import { dayText, longDate } from "@/lib/specials";
import { NOT_FINAL, reportingText } from "@/lib/results";
import { PARTY_COLORS } from "@/lib/partyStyles";
import { CALENDAR_HREF } from "@/lib/routes";
import { INLINE_LINK } from "@/lib/controlStyles";

/*
  Special elections as the backend serves them (app/special_elections.py):
  the seats empty today, the specials ahead, and the year's held ones with
  the state's own count and, once settled, the swing against the seat's
  usual vote. Pure render, no hooks, so the calendar page (server) and the
  /elections lead (client) draw the same thing. Every figure and sentence
  is the backend's; nothing here calls a race.
*/

/** Why a special has no count, in words: "which absence it is". */
export function noCountText(s: SpecialElection): string | null {
  if (s.count) return null;
  if (s.liveCount === "not_read_type")
    return "A special primary or party convention: no count is read for it.";
  if (s.liveCount === "not_covered")
    return `No count shown: Civitas has no reader for ${s.stateName}'s results feed. The state's election office publishes it.`;
  if (s.status === "upcoming" || s.status === "voting")
    return "The count is read from the state's own feed once its polls close.";
  if (s.readStatus === "untrusted")
    return "The state's feed was refused (test or mismatched data).";
  if (s.readStatus === "failed" || s.readStatus === "unavailable")
    return "The state's feed couldn't be read; Civitas keeps trying.";
  if (s.readStatus === "not_listed") return "The state's feed doesn't list this contest yet.";
  if (s.readStatus === "ambiguous")
    return "The state's feed lists two contests for this seat; neither is shown.";
  return "No count is stored: this special was not read while its count was open.";
}

function SpecialCountLines({ s }: { s: SpecialElection }) {
  const c = s.count;
  if (!c) return null;
  const reporting = reportingText({
    reportingUnits: c.reportingUnits,
    totalUnits: c.totalUnits,
    unitLabel: c.unitLabel ?? undefined,
    unitsComplete: c.unitsComplete,
  });
  const source = safeHref(c.sourceUrl);
  return (
    <div className="mt-2">
      <p className="font-mono text-[11px] tracking-[0.12em] text-signal-amber">
        {c.official ? "LEADS · OFFICIAL COUNT" : `LEADING · ${NOT_FINAL}`}
      </p>
      <ul className="mt-1 space-y-0.5">
        {c.tallies.slice(0, 3).map((t) => (
          <li key={t.name} className="flex justify-between gap-3 font-mono text-xs text-ink-lo">
            <span>
              {t.name}
              {t.party && (
                <span className={`ml-1.5 ${PARTY_COLORS[t.party] ?? ""}`}>{t.party}</span>
              )}
            </span>
            <span className="tabular-nums text-ink-hi">{t.votes.toLocaleString("en-US")}</span>
          </li>
        ))}
      </ul>
      <p className="mt-1 text-xs text-ink-min">
        {[reporting, c.sourceName].filter(Boolean).join(" · ")}
        {source && (
          <>
            {" · "}
            <a href={source} className={INLINE_LINK} rel="noopener">
              the state&apos;s count
            </a>
          </>
        )}
      </p>
    </div>
  );
}

export function SpecialItem({ s }: { s: SpecialElection }) {
  const absent = noCountText(s);
  return (
    <li id={`special-${s.id}`} className="border-t border-white/[0.07] py-3">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <p className="font-display text-base font-semibold text-ink-hi">{s.seat}</p>
        <p className="font-mono text-[11px] tracking-[0.12em] text-ink-min">
          {longDate(s.date).toUpperCase()} · {dayText(s.status, s.daysLeft)}
        </p>
      </div>
      <p className="mt-0.5 text-sm text-ink-lo">
        {s.label ?? "Special election"}
        {s.note && <span className="text-ink-min"> · {s.note}</span>}
      </p>
      <SpecialCountLines s={s} />
      {s.swing && <p className="mt-2 text-sm text-ink-hi">{s.swing.sentence}</p>}
      {absent && <p className="mt-1 text-xs text-ink-min">{absent}</p>}
    </li>
  );
}

function VacancyItem({ v }: { v: SeatVacancy }) {
  const source = safeHref(v.sourceUrl);
  return (
    <li className="border-t border-white/[0.07] py-3">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <p className="font-display text-base font-semibold text-ink-hi">{v.seat}</p>
        {v.vacatedOn && (
          <p className="font-mono text-[11px] tracking-[0.12em] text-ink-min">
            VACANT SINCE {longDate(v.vacatedOn).toUpperCase()}
          </p>
        )}
      </div>
      <p className="mt-0.5 text-sm text-ink-lo">
        {v.footnote ?? (v.office === "S" ? "No senator is listed for this seat." : "Vacant.")}{" "}
        {source && (
          <a href={source} className={INLINE_LINK} rel="noopener">
            {v.office === "H" ? "House Clerk" : "Senate"}
          </a>
        )}
      </p>
      <p className="mt-1 text-xs text-ink-min">
        {v.nextSpecial ? (
          <>
            Next: {v.nextSpecial.label ?? "special election"}, {longDate(v.nextSpecial.date)}.
          </>
        ) : (
          "No special election for this seat is on the FEC's calendar yet."
        )}
      </p>
    </li>
  );
}

/** Held specials shown open before the rest fold away: the newest few,
 * so the calendar below isn't pushed screens down. */
const RECENT_OPEN = 5;

function H3({ children }: { children: React.ReactNode }) {
  return (
    <h3 className="mt-6 font-mono text-xs uppercase tracking-[0.16em] text-ink-min">{children}</h3>
  );
}

/** The board: vacancies, specials ahead, the year's held specials with the
 * average swing, and when the year has none, the last one. `limit` caps
 * each list (the /elections lead shows a few and links the rest). */
export default function SpecialsBoardView({
  board,
  limit,
}: {
  board: SpecialsBoard;
  limit?: number;
}) {
  const cap = <T,>(xs: T[]) => (limit ? xs.slice(0, limit) : xs);
  const avg = board.averageSwing;
  return (
    <div>
      <H3>Vacant seats</H3>
      {board.vacancies.length ? (
        <ul className="mt-1">
          {cap(board.vacancies).map((v) => (
            <VacancyItem key={v.id} v={v} />
          ))}
        </ul>
      ) : (
        <p className="mt-2 text-sm text-ink-lo">
          Every seat in Congress is filled, by the House Clerk&apos;s and the Senate&apos;s own
          member lists.
        </p>
      )}

      <H3>Special elections ahead</H3>
      {board.upcoming.length ? (
        <ul className="mt-1">
          {cap(board.upcoming).map((s) => (
            <SpecialItem key={s.id} s={s} />
          ))}
        </ul>
      ) : (
        <p className="mt-2 text-sm text-ink-lo">
          No federal special election is on the FEC&apos;s calendar.
        </p>
      )}

      <H3>Held in the last year</H3>
      {board.recent.length ? (
        <>
          <p className="mt-2 text-sm text-ink-lo">
            {avg.sentence}
            {avg.swing != null &&
              " A seat's usual vote is its presidential lean, so a swing mixes the national mood with the race itself."}
          </p>
          <ul className="mt-1">
            {cap(board.recent)
              .slice(0, RECENT_OPEN)
              .map((s) => (
                <SpecialItem key={s.id} s={s} />
              ))}
          </ul>
          {cap(board.recent).length > RECENT_OPEN && (
            <details>
              <summary className="flex min-h-11 cursor-pointer items-center font-mono text-xs uppercase tracking-[0.14em] text-ink-min">
                {cap(board.recent).length - RECENT_OPEN} more held in the last year
              </summary>
              <ul>
                {cap(board.recent)
                  .slice(RECENT_OPEN)
                  .map((s) => (
                    <SpecialItem key={s.id} s={s} />
                  ))}
              </ul>
            </details>
          )}
        </>
      ) : board.lastSpecial ? (
        <p className="mt-2 text-sm text-ink-lo">
          None. The last federal special election on file was for {board.lastSpecial.seat}, on{" "}
          {longDate(board.lastSpecial.date)}.
        </p>
      ) : (
        <p className="mt-2 text-sm text-ink-lo">None on file.</p>
      )}
      {limit && (
        <Link
          href={`${CALENDAR_HREF}#specials-board`}
          className="mt-3 inline-flex min-h-11 items-center font-mono text-xs tracking-[0.1em] text-signal-cyan hover:text-phos"
        >
          [ EVERY SPECIAL ELECTION → ]
        </Link>
      )}
    </div>
  );
}
