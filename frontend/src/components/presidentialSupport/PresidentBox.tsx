"use client";

import Link from "@/components/Link";
import RateTrack from "@/components/presidentialSupport/RateTrack";
import VoteList from "@/components/presidentialSupport/VoteList";
import MethodNotes from "@/components/presidentialSupport/MethodNotes";
import MemberBox from "@/components/scorecard/MemberBox";
import ShareSectionButton from "@/components/share/ShareSectionButton";
import { ShareSubjectProvider } from "@/components/share/ShareSubjectContext";
import { fetchMemberPresidentialSupport } from "@/lib/api";
import { useAsyncData } from "@/hooks/useAsyncData";
import {
  expectationSentence,
  leanLabel,
  presidentSupportHref,
  recordSentence,
} from "@/lib/presidentialSupport";
import { absoluteUrl } from "@/lib/site";
import { SHARE_EXCLUDE_ATTR } from "@/lib/shareImage";
import type { MemberBox as Box } from "@/types/politicians";
import type { MemberSupport } from "@/types/presidentialSupport";

/**
 * A member's votes with the president's stated positions, as one box of the
 * record: the backend's figure, its line ("15%, against 21% ...: about as
 * expected") and a square per counted vote; behind More the full sentences,
 * the rate against the seat's norm, every vote with its Statement of
 * Administration Policy, the method and the data. The page's only place for
 * it (the line in the Votes column and the section lower down are gone).
 */
export default function PresidentBox({
  memberId,
  name,
  box,
}: {
  memberId: string;
  name: string;
  /** Null when the member has no counted vote; undefined from an older
   *  backend, when the fetched record decides. */
  box: (Box & { finding?: string }) | null | undefined;
}) {
  const { data } = useAsyncData<MemberSupport | null>(`presidential-support:${memberId}`, () =>
    fetchMemberPresidentialSupport(memberId)
  );
  if (box === null || (box === undefined && !data)) return null;

  // Oldest first, so the tally reads left to right through the Congress.
  const counted = data ? data.rollCalls.filter((v) => v.withPresident != null).reverse() : [];
  const jsonHref = `/api/presidential-support/member/${encodeURIComponent(memberId)}`;
  const subject = {
    title: name,
    subtitle: "Votes with the president's stated positions",
    url: absoluteUrl(`/politicians/${encodeURIComponent(memberId)}`),
    source: data
      ? `${data.method.source}; roll calls as ${data.chamber === "senate" ? "Senate.gov" : "the House Clerk"} records them`
      : "Statements of Administration Policy, Office of Management and Budget",
    asOf: data?.method.computedAt ?? null,
  };

  return (
    <ShareSubjectProvider subject={subject}>
      <MemberBox
        id="with-the-president"
        label="With the president"
        box={box}
        fallback={{ kicker: "With the president", title: "With the president" }}
        more={{
          label: "the votes, the method and the data",
          children: data ? (
            <div className="flex flex-col gap-4">
              <p className="text-body text-ink">
                {recordSentence(name, data)} {expectationSentence(data)}
              </p>
              <RateTrack s={data} label={`${name}, votes with the president's stated position`} />
              <p className="text-small text-ink-min">
                n = {data.votes} · rate {data.rate} · 95% range {data.rateLow} to {data.rateHigh} ·
                expected {data.expected ?? "n/a"} · seat {leanLabel(data.seatLean)}
              </p>
              <MethodNotes method={data.method} jsonHref={jsonHref} />
              <VoteList votes={data.rollCalls} member={name} />
              <div
                className="flex flex-wrap items-center gap-x-4"
                {...{ [SHARE_EXCLUDE_ATTR]: "" }}
              >
                <Link
                  href={presidentSupportHref(data.chamber)}
                  className="inline-flex min-h-11 items-center text-small text-ink underline underline-offset-2 hover:text-phos"
                >
                  Compare the whole {data.chamber === "senate" ? "Senate" : "House"} &rarr;
                </Link>
                <ShareSectionButton label="With the president" />
              </div>
            </div>
          ) : (
            <p className="text-small text-ink-lo">Loading the votes...</p>
          ),
        }}
      >
        {counted.length > 0 && (
          <div>
            {/* The squares are the votes VoteList lists behind More; drawn
                here as a picture of the tally. */}
            <div
              role="img"
              aria-label={`${data!.withPresident} of ${data!.votes} counted votes on the president's stated side`}
              className="flex flex-wrap gap-[3px]"
            >
              {counted.map((v) => (
                <span
                  key={`${v.chamber}-${v.session}-${v.rollCall}`}
                  className={`block h-2.5 w-2.5 border ${
                    v.withPresident ? "border-ink-hi bg-ink-hi" : "border-ink-min bg-transparent"
                  }`}
                />
              ))}
            </div>
            <p className="mt-1.5 text-small text-ink-min">
              Each square is one counted vote, oldest first · filled: on the president&apos;s side
            </p>
          </div>
        )}
      </MemberBox>
    </ShareSubjectProvider>
  );
}
