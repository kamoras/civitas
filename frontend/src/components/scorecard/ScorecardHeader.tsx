import Link from "next/link";
import type { Senator } from "@/types/senator";
import type { Committee } from "@/types/politicians";
import { asLabel, displayScore, safeHref } from "@/lib/formatting";
import { getScoreColor, getScoreLabel } from "@/lib/representation";
import { currentCongressLabel } from "@/lib/sources";
import { districtName } from "@/lib/elections";
import { PARTY_BORDER, PARTY_COLORS, PARTY_LABELS } from "@/lib/partyStyles";
import MetricTooltip from "@/components/checker/MetricTooltip";
import ScoreTrendSection from "@/components/checker/ScoreTrendSection";
import ShareSectionButton from "@/components/share/ShareSectionButton";
import { SHARE_EXCLUDE_ATTR, SHARE_SECTION_ATTR } from "@/lib/shareImage";

/**
 * `min-h-6` for WCAG 2.2 SC 2.5.8 (24x24 targets): the links sit in a
 * wrapping row, and ones hemmed in on both sides don't get the spacing
 * exception.
 */
const LINK =
  "inline-flex min-h-6 items-center font-mono text-[13px] text-ink-lo underline underline-offset-2 hover:text-phos";

/** Who the member is, how to reach them, and the verdict: the
 *  Representation Score, the chamber rank the leaderboard gives it, and how
 *  it has moved (scoring-method changes marked on the trend). */
export default function ScorecardHeader({
  member,
  chamber,
  thumbnailUrl,
  stateName,
  district,
  leadershipTitle,
  committees,
  rank,
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
  rank?: { rank: number; of: number } | null;
  titleAs: "h1" | "h2";
  former?: boolean;
}) {
  const overall = displayScore(member.representationScore.overall);
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

  return (
    <header
      id="overview"
      {...{ [SHARE_SECTION_ATTR]: "overview" }}
      className={`grid scroll-mt-[var(--header-clearance)] gap-6 border border-white/25 border-t-[3px] bg-surface px-5 py-6 font-sans sm:px-8 lg:grid-cols-[minmax(0,1fr)_minmax(0,24rem)] ${PARTY_BORDER[member.party]}`}
    >
      {/* Three blocks in reading order for a phone — who, the score, then
          how to reach them; on a desktop the score takes the right column
          beside both. Below sm the photo sits above the name: beside it, the
          name had about 146px at 320px and long surnames split mid-word. */}
      <div className="flex min-w-0 flex-col gap-5 sm:flex-row lg:col-start-1 lg:row-start-1">
        {thumbnailUrl ? (
          // eslint-disable-next-line @next/next/no-img-element -- external, varied politician-photo hosts
          <img
            src={thumbnailUrl}
            alt={member.name}
            className={`h-24 w-20 shrink-0 border-2 object-cover sm:h-28 sm:w-24 ${PARTY_BORDER[member.party]}`}
          />
        ) : (
          <div
            className={`flex h-24 w-20 shrink-0 items-center justify-center border-2 text-2xl font-bold sm:h-28 sm:w-24 ${PARTY_BORDER[member.party]} ${PARTY_COLORS[member.party]}`}
            aria-hidden="true"
          >
            {member.initials}
          </div>
        )}
        <div className="flex min-w-0 flex-col gap-2">
          <Title className="break-words text-3xl font-extrabold leading-tight text-ink-hi sm:text-4xl">
            {member.name}
          </Title>
          <p className="font-mono text-xs uppercase tracking-[0.12em] text-ink-lo">
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

      <div className="flex flex-col gap-2 border-t border-white/[0.12] pt-5 lg:col-start-2 lg:row-span-2 lg:row-start-1 lg:border-l lg:border-t-0 lg:pl-8 lg:pt-0">
        <p className="font-mono text-xs uppercase tracking-[0.14em] text-ink-min">
          <MetricTooltip text="The weighted average of the three scores below. 100 means full representation of the constituents by these measures; a score near 50 often means limited data.">
            Representation Score
          </MetricTooltip>
        </p>
        <div className="flex items-baseline gap-4">
          <span
            className={`font-display text-7xl font-extrabold leading-none ${getScoreColor(overall)}`}
          >
            {overall}
          </span>
          <div className="flex flex-col gap-1">
            <span className={`font-mono text-sm tracking-[0.1em] ${getScoreColor(overall)}`}>
              {getScoreLabel(overall)}
            </span>
            {rank && (
              <Link
                href={`/leaderboard?branch=${chamber}`}
                className="font-mono text-[13px] text-ink-lo underline underline-offset-2 hover:text-phos"
              >
                #{rank.rank} of {rank.of} {chamber === "senate" ? "senators" : "representatives"}
              </Link>
            )}
          </div>
        </div>
        <ScoreTrendSection entityId={member.id} entityType={chamber} />
        <div className="flex flex-wrap items-center justify-between gap-3">
          <p className="font-mono text-xs text-ink-min">
            Reflects the {currentCongressLabel()} ·{" "}
            <Link href="/about/scores" className="underline underline-offset-2 hover:text-phos">
              how scores are computed
            </Link>{" "}
            ·{" "}
            <Link href="/changelog" className="underline underline-offset-2 hover:text-phos">
              scoring changelog
            </Link>
          </p>
          {/* The header names the member itself, so its image needs no
              title strip above it. */}
          <ShareSectionButton label="Scorecard summary" withStrip={false} />
        </div>
      </div>

      <div className="flex min-w-0 flex-col gap-2 lg:col-start-1 lg:row-start-2 lg:pl-[calc(6rem+1.25rem)]">
        {committees && committees.length > 0 && (
          <p className="text-sm text-ink-lo">
            Committees:{" "}
            {committees.map((c, i) => (
              <span key={c.committeeName}>
                {i > 0 && " · "}
                {c.committeeName.replace(/^(House|Senate) Committee on /, "")}
                {c.title && (
                  <span className="font-mono text-xs uppercase text-ink-min"> ({c.title})</span>
                )}
              </span>
            ))}
          </p>
        )}
        {/* Phone, message and source links are only useful clicked; a
            picture of them is noise. */}
        <p className="flex flex-wrap gap-x-4 gap-y-1" {...{ [SHARE_EXCLUDE_ATTR]: "" }}>
          {phone && (
            <a href={`tel:${phone.replace(/[^0-9+]/g, "")}`} className={LINK}>
              {phone}
            </a>
          )}
          {!former && member.contactFormUrl && (
            <a
              href={safeHref(member.contactFormUrl) || "#"}
              target="_blank"
              rel="noopener noreferrer"
              className={LINK}
            >
              Send a message ↗
            </a>
          )}
          {!former && member.websiteUrl && (
            <a
              href={safeHref(member.websiteUrl) || "#"}
              target="_blank"
              rel="noopener noreferrer"
              className={LINK}
            >
              Official site ↗
            </a>
          )}
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
          <Link href={`/compare?leftId=${member.id}&leftChamber=${chamber}`} className={LINK}>
            Compare with another member
          </Link>
        </p>
        {!former && member.officeAddress && (
          <p className="text-xs text-ink-min">DC office: {member.officeAddress}</p>
        )}
      </div>
    </header>
  );
}
