import Link from "next/link";
import { onThisPage } from "@/lib/onThisPage";
import type { Justice } from "@/types/justice";
import { SectionHeadingLevelProvider } from "@/components/shared/CollapsibleSection";
import ShareSectionButton from "@/components/share/ShareSectionButton";
import { ShareSubjectProvider } from "@/components/share/ShareSubjectContext";
import { SHARE_EXCLUDE_ATTR, SHARE_SECTION_ATTR, type ShareSubject } from "@/lib/shareImage";
import { absoluteUrl } from "@/lib/site";
import Photo from "@/components/Photo";
import JusticeCases from "@/components/scorecard/JusticeCases";
import { COURT_HREF } from "@/lib/court";
import AppointerIntervals from "./AppointerIntervals";
import JusticeFigures from "./JusticeFigures";

const PARTY: Record<string, { label: string; text: string; border: string }> = {
  D: { label: "D", text: "text-dem-blue", border: "border-dem-blue/40" },
  R: { label: "R", text: "text-signal-red", border: "border-signal-red/40" },
};
const NO_PARTY = { label: "", text: "text-ink", border: "border-white/30" };

const LINK = "font-mono text-[13px] text-ink-lo underline underline-offset-2 hover:text-phos";

function formatDate(iso: string | null) {
  if (!iso) return null;
  const d = new Date(`${iso.slice(0, 10)}T00:00:00Z`);
  return Number.isNaN(d.getTime())
    ? null
    : d.toLocaleDateString("en-US", {
        month: "short",
        day: "numeric",
        year: "numeric",
        timeZone: "UTC",
      });
}

/** Who appointed the justice, linked to that president's page: the story
 *  of the Court runs through the presidents who filled its seats. */
function AppointedBy({ justice: j }: { justice: Justice }) {
  const by = j.figures?.appointedBy;
  const name = by?.name ?? j.appointingPresident;
  if (!name) return null;
  const party = (j.appointingParty && PARTY[j.appointingParty]) || NO_PARTY;
  const year = by?.year ?? (j.dateStart ? Number(j.dateStart.slice(0, 4)) : null);
  const body = (
    <>
      <span className="block text-[13px] font-semibold text-ink-lo">Appointed by</span>
      <span className="block font-display text-2xl font-semibold leading-tight text-ink-hi">
        {name}
        {party.label && <span className={`text-lg ${party.text}`}> ({party.label})</span>}
      </span>
      {year && <span className="block text-sm text-ink">Joined the Court in {year}</span>}
    </>
  );
  return by?.id ? (
    <Link
      href={`/politicians/${encodeURIComponent(by.id)}`}
      className="group block border border-white/25 bg-surface-raised px-4 py-3 hover:border-phos"
    >
      {body}
      <span className="mt-1 block text-sm font-semibold text-phos group-hover:underline">
        The president&apos;s record ▸
      </span>
    </Link>
  ) : (
    <div className="border border-white/25 bg-surface-raised px-4 py-3">{body}</div>
  );
}

/**
 * A justice's page: who, who appointed them, then the record drawn beside
 * every sitting justice's, the appointer estimate with each justice's
 * interval, agreement with each colleague and every case. Records and
 * measurements only, no score (justice v3). Every number is the API's.
 */
export default function JusticeProfile({
  justice: j,
  titleAs: Title = "h1",
}: {
  justice: Justice;
  titleAs?: "h1" | "h2";
}) {
  const party = (j.appointingParty && PARTY[j.appointingParty]) || NO_PARTY;
  const since = formatDate(j.dateStart);
  // Named and ordered by the API (most agreement first). Absent from a
  // response cached before the field replaced agreementMatrix.
  const agreement = j.agreement ?? [];
  const figures = j.figures ?? null;
  const shareSubject: ShareSubject = {
    title: j.name,
    subtitle: [j.roleTitle, j.appointingPresident && `appointed by ${j.appointingPresident}`]
      .filter(Boolean)
      .join(" · "),
    url: absoluteUrl(`/politicians/${encodeURIComponent(j.id)}`),
    source: "Supreme Court Database; Martin-Quinn scores; Oyez",
    asOf: null,
  };

  return (
    <SectionHeadingLevelProvider value="h3">
      <ShareSubjectProvider subject={shareSubject}>
        <div className="flex flex-col gap-6">
          <header
            id="overview"
            {...{ [SHARE_SECTION_ATTR]: "overview" }}
            className={`grid grid-cols-[minmax(0,1fr)] gap-6 border border-white/25 border-t-[3px] bg-surface px-5 py-6 font-sans sm:px-8 lg:grid-cols-[minmax(0,1fr)_minmax(0,22rem)] ${party.border}`}
          >
            {/* Portrait above the name below sm: beside it, the name had
                about 146px at 320px and long surnames split mid-word. */}
            <div className="flex min-w-0 flex-col gap-5 sm:flex-row">
              {j.thumbnailUrl && (
                <Photo
                  src={j.thumbnailUrl}
                  alt=""
                  sizes="96px"
                  className={`h-28 w-24 shrink-0 border-2 object-cover ${party.border}`}
                />
              )}
              <div className="flex min-w-0 flex-col gap-2">
                <Title className="break-words text-3xl font-semibold leading-tight text-ink-hi sm:text-4xl">
                  {j.name}
                </Title>
                <p className="font-mono text-xs text-ink-lo">
                  {j.roleTitle}
                  {since && ` · since ${since}`}
                </p>
                <p className="flex flex-wrap gap-x-4 gap-y-1" {...{ [SHARE_EXCLUDE_ATTR]: "" }}>
                  <Link href={COURT_HREF} className={LINK}>
                    The whole Court
                  </Link>
                  <a href="https://www.oyez.org" className={LINK}>
                    Oyez
                  </a>
                  <a href="http://scdb.la.psu.edu" className={LINK}>
                    Supreme Court Database
                  </a>
                </p>
              </div>
            </div>
            <div className="flex flex-col gap-3">
              <AppointedBy justice={j} />
              {/* The header names the justice itself: no title strip. */}
              <div className="flex justify-end">
                <ShareSectionButton label="Overview" withStrip={false} />
              </div>
            </div>
          </header>

          {figures && figures.measures.length > 0 && (
            <JusticeFigures figures={figures} justiceId={j.id} name={j.name} />
          )}

          {agreement.length > 0 && (
            <section
              id="agreement"
              {...{ [SHARE_SECTION_ATTR]: "agreement" }}
              className="flex flex-col gap-3 border border-white/25 bg-surface px-5 py-5 font-sans"
              aria-labelledby="agreement-heading"
            >
              <h2
                id="agreement-heading"
                {...onThisPage("Agreement")}
                className="text-[19px] font-semibold text-ink-hi"
              >
                Agreement with each justice
              </h2>
              <p className="text-sm text-ink">
                Who votes alike: the share of cases both decided where they took the same side.
              </p>
              <figure aria-labelledby="agreement-caption" className="flex flex-col gap-2">
                <ul className="grid grid-cols-[minmax(0,1fr)] gap-x-8 gap-y-2 sm:grid-cols-[repeat(2,minmax(0,1fr))]">
                  {agreement.map(({ id, name, share }) => (
                    <li key={id} className="flex flex-wrap items-center gap-x-3 gap-y-1">
                      <Link
                        href={`/politicians/${encodeURIComponent(id)}`}
                        className="w-40 max-w-full truncate text-sm text-ink underline-offset-2 hover:text-phos hover:underline"
                      >
                        {name}
                      </Link>
                      <span className="flex min-w-32 flex-1 items-center gap-3">
                        <span className="h-2 flex-1 bg-[var(--chart-faint)]" aria-hidden="true">
                          <span className="block h-full bg-ink-lo" style={{ width: `${share}%` }} />
                        </span>
                        <span className="w-12 text-right font-mono text-sm tabular-nums text-ink-hi">
                          {Math.round(share)}%
                        </span>
                      </span>
                    </li>
                  ))}
                </ul>
                <figcaption id="agreement-caption" className="text-sm text-ink">
                  Most often on the same side as {agreement[0].name} (
                  {Math.round(agreement[0].share)}%), least often as{" "}
                  {agreement[agreement.length - 1].name} (
                  {Math.round(agreement[agreement.length - 1].share)}%).{" "}
                  <Link
                    href={`${COURT_HREF}#agreement`}
                    className="text-phos underline underline-offset-2"
                  >
                    Every pair on the Court
                  </Link>
                </figcaption>
              </figure>
              <div className="flex justify-end">
                <ShareSectionButton label="Agreement with each justice" />
              </div>
            </section>
          )}

          {figures?.appointer && (
            <AppointerIntervals appointer={figures.appointer} justiceId={j.id} />
          )}

          <JusticeCases justiceId={j.id} />
        </div>
      </ShareSubjectProvider>
    </SectionHeadingLevelProvider>
  );
}
