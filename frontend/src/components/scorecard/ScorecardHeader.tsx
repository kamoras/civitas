import type { Senator } from "@/types/senator";
import type { Committee } from "@/types/politicians";
import { asLabel, safeHref } from "@/lib/formatting";
import { districtName } from "@/lib/elections";
import { PARTY_BORDER, PARTY_COLORS, PARTY_LABELS } from "@/lib/partyStyles";
import MetricTooltip from "@/components/checker/MetricTooltip";
import { SHARE_EXCLUDE_ATTR, SHARE_SECTION_ATTR } from "@/lib/shareImage";
import Photo from "@/components/Photo";
import { onThisPage } from "@/lib/onThisPage";
import { LONG_LABEL } from "@/lib/controlStyles";

/**
 * `min-h-6` for WCAG 2.2 SC 2.5.8 (24x24 targets): the links sit in a
 * wrapping row, and ones hemmed in on both sides don't get the spacing
 * exception.
 */
const LINK =
  "inline-flex min-h-6 items-center font-mono text-[13px] text-ink-lo underline underline-offset-2 hover:text-phos";

const BUTTON =
  "inline-flex min-h-11 items-center border border-ink-hi px-3 text-sm font-semibold text-ink-hi wrap-anywhere hover:bg-white/5";

/** A contact form's host, printed on its button ("wyden.senate.gov"). */
function hostOf(url: string): string {
  try {
    return new URL(url).host.replace(/^www\./, "");
  } catch {
    return "contact form";
  }
}

/** Who the member is and how to reach them. No verdict: the composite
 *  score and its rank are no longer shown (redesign 2026-10); where the
 *  member stands is WhereTheyStand, measure by measure, against peers. */
export default function ScorecardHeader({
  member,
  chamber,
  thumbnailUrl,
  stateName,
  district,
  leadershipTitle,
  committees,
  titleAs: Title,
  former = false,
}: {
  member: Senator;
  chamber: "senate" | "house";
  thumbnailUrl?: string | null;
  stateName?: string | null;
  district?: number | null;
  leadershipTitle?: string | null;
  committees?: Committee[];
  titleAs: "h1" | "h2";
  former?: boolean;
}) {
  const office = chamber === "senate" ? "Senator" : "Representative";
  const place = [stateName ?? member.state, district != null ? districtName(district) : null]
    .filter(Boolean)
    .join(" · ");
  const years = member.yearsInOffice;
  const tenure =
    years < 1
      ? "less than a year in office"
      : `${years} ${years === 1 ? "year" : "years"} in office`;
  const fecOffice = chamber === "senate" ? "S" : "H";
  // A departed member's office line, contact form and site now reach the
  // successor's office (it inherits the suite and phone), or nothing.
  const phone = former ? "" : member.officePhone;

  const initials = (
    <div
      className={`flex h-14 w-12 shrink-0 items-center justify-center border-2 text-lg font-semibold sm:h-28 sm:w-24 sm:text-2xl ${PARTY_BORDER[member.party]} ${PARTY_COLORS[member.party]}`}
      aria-hidden="true"
    >
      {member.initials}
    </div>
  );

  return (
    <header
      id="overview"
      {...{ [SHARE_SECTION_ATTR]: "overview" }}
      {...onThisPage("Overview")}
      className={`grid grid-cols-[minmax(0,1fr)] gap-4 border border-white/25 border-t-[3px] bg-surface px-5 py-6 font-sans sm:px-8 ${PARTY_BORDER[member.party]}`}
    >
      {/* Three blocks in reading order for a phone — who, the score, then
          how to reach them; on a desktop the score takes the right column
          beside both. Below sm the photo is small, so the score, the
          scorecard in short and the strip make a phone's first screen; a long
          surname that still doesn't fit hyphenates at a syllable rather than
          splitting anywhere (it once had 146px at 320px beside a full-size
          photo, and split mid-word). */}
      <div className="flex min-w-0 flex-row flex-wrap gap-3 sm:gap-5">
        {thumbnailUrl ? (
          <Photo
            src={thumbnailUrl}
            alt={member.name}
            sizes="(min-width: 640px) 96px, 48px"
            className={`h-14 w-12 shrink-0 border-2 object-cover object-[center_25%] sm:h-28 sm:w-24 ${PARTY_BORDER[member.party]}`}
            fallback={initials}
          />
        ) : (
          initials
        )}
        {/* At least 12rem beside the photo, or under it: at a large text
            size on a phone the name had about 110 px beside the photo and
            broke mid-word (WCAG 1.4.10). */}
        <div className="flex min-w-0 flex-1 basis-48 flex-col gap-2">
          <Title
            lang="en"
            className="break-words text-2xl font-semibold leading-tight text-ink-hi [hyphens:auto] sm:text-4xl"
          >
            {member.name}
          </Title>
          <p
            className={`font-mono tracking-[0.08em] text-ink-lo sm:tracking-[0.12em] ${LONG_LABEL}`}
          >
            {office} · {place} · {PARTY_LABELS[member.party] ?? member.party} · {tenure}
          </p>
          {(leadershipTitle || member.sponsorshipDescription) && (
            <p className="flex flex-wrap items-center gap-2 text-sm text-ink-lo">
              {leadershipTitle && (
                <span className="border border-signal-amber/40 bg-signal-amber/10 px-2 py-0.5 font-mono text-xs uppercase tracking-[0.12em] text-signal-amber">
                  {leadershipTitle}
                </span>
              )}
              {member.sponsorshipDescription && (
                <MetricTooltip text="From who cosponsors whose bills this Congress. The ideology word is the member's third of their own party on a left-right position read from cosponsorships (SVD), not from votes; leader or follower is their PageRank position in the chamber's cosponsorship network, pulled toward the middle for under six years in office.">
                  {asLabel(member.sponsorshipDescription)}
                </MetricTooltip>
              )}
            </p>
          )}
        </div>
      </div>

      <div className="flex min-w-0 flex-col gap-3 sm:pl-[calc(6rem+1.25rem)]">
        {committees && committees.length > 0 && (
          <ul aria-label="Committees" className="flex flex-wrap gap-1.5 text-sm text-ink">
            {committees.map((c) => (
              <li key={c.committeeName} className="border border-white/30 px-2 py-0.5">
                {c.committeeName.replace(/^(House|Senate) Committee on /, "")}
                {c.title && <span className="font-semibold"> · {c.title}</span>}
              </li>
            ))}
          </ul>
        )}
        {/* Phone, message and source links are only useful clicked; a
            picture of them is noise. */}
        <div className="flex flex-wrap gap-2" {...{ [SHARE_EXCLUDE_ATTR]: "" }}>
          {phone && (
            <a
              href={`tel:${phone.replace(/[^0-9+]/g, "")}`}
              className={`${BUTTON} bg-phos text-surface-base`}
            >
              Call {phone}
            </a>
          )}
          {!former && member.contactFormUrl && (
            <a
              href={safeHref(member.contactFormUrl) || "#"}
              target="_blank"
              rel="noopener noreferrer"
              className={BUTTON}
            >
              Write · {hostOf(member.contactFormUrl)} ↗
            </a>
          )}
          {!former && member.websiteUrl && (
            <a
              href={safeHref(member.websiteUrl) || "#"}
              target="_blank"
              rel="noopener noreferrer"
              className={BUTTON}
            >
              Website ↗
            </a>
          )}
        </div>
        <p className="flex flex-wrap gap-x-4 gap-y-1" {...{ [SHARE_EXCLUDE_ATTR]: "" }}>
          {/* Congress.gov's member pages are keyed by Bioguide id; the name
              part of the path is decoration (it redirects to its own
              spelling). A name alone is a "Page Not Found". */}
          {member.bioguideId && (
            <a
              href={`https://www.congress.gov/member/${member.name.toLowerCase().replace(/\s+/g, "-")}/${member.bioguideId}`}
              target="_blank"
              rel="noopener noreferrer"
              className={LINK}
            >
              Congress.gov ↗
            </a>
          )}
          <a
            href={`https://www.fec.gov/data/candidates/?search=${encodeURIComponent(member.name)}&office=${fecOffice}`}
            target="_blank"
            rel="noopener noreferrer"
            className={LINK}
          >
            FEC filings ↗
          </a>
          <a href="#methods" className={LINK}>
            Methods, data &amp; citation
          </a>
        </p>
        {!former && member.officeAddress && (
          <p className="text-xs text-ink-min">DC office: {member.officeAddress}</p>
        )}
      </div>
    </header>
  );
}
