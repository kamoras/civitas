import { Metadata } from "next";
import { notFound } from "next/navigation";
import type { VoteDetail } from "@/types/congress";
import { fetchRollCall } from "@/lib/congressServer";
import { CHAMBER_NAME, dayHref, longDate, rollCallHref } from "@/lib/congress";
import { describeRollCall } from "@/lib/seo";
import { TOPIC_FEEDS, absoluteUrl, pageMetadata } from "@/lib/site";
import JsonLd, { breadcrumbList } from "@/components/seo/JsonLd";
import RollCallView from "@/components/congress/RollCallView";

type Params = { chamber: string; congress: string; session: string; number: string };
type PageProps = { params: Promise<Params> };

/** A roll call's address as the record writes it: no leading zeros, so one
 * vote has one URL. Anything else is no page (the backend's ranges then
 * decide what exists). */
const NUMBER = /^[1-9]\d{0,3}$/;

/** The vote the address names, or null for an address that can't name one
 * or a vote not recorded. An outage throws (congressServer), and
 * congress/error.tsx says the record could not be reached. */
async function load({ chamber, congress, session, number }: Params): Promise<VoteDetail | null> {
  if (!(chamber in CHAMBER_NAME) || ![congress, session, number].every((n) => NUMBER.test(n)))
    return null;
  return fetchRollCall(chamber, congress, session, number);
}

export async function generateMetadata({ params }: PageProps): Promise<Metadata> {
  const p = await params;
  const vote = await load(p);
  if (!vote)
    return pageMetadata({
      title: "Vote not found",
      description: "No roll call recorded at this address.",
      path: `/congress/votes/${p.chamber}/${p.congress}/${p.session}/${p.number}`,
      noindex: true,
    });
  const meta = describeRollCall(vote);
  return pageMetadata({
    ...meta,
    path: rollCallHref(vote),
    feed: TOPIC_FEEDS.congress,
    type: "article",
    // The roll call's own card (tally by party, source, as-of), not the
    // site card: a shared vote previews as the vote.
    images: [
      {
        url: absoluteUrl(
          `/api/og?vote=${vote.chamber}/${vote.congress}/${vote.session}/${vote.number}`
        ),
        width: 1200,
        height: 630,
        alt: meta.title,
      },
    ],
  });
}

export default async function RollCallPage({ params }: PageProps) {
  const vote = await load(await params);
  if (!vote) notFound();
  const path = rollCallHref(vote);
  const name = `${CHAMBER_NAME[vote.chamber]} roll call ${vote.number}`;
  return (
    <>
      <JsonLd
        data={breadcrumbList([
          { name: "Home", url: absoluteUrl("/") },
          { name: "Congress", url: absoluteUrl("/congress") },
          { name: longDate(vote.date), url: absoluteUrl(dayHref(vote.date)) },
          { name, url: absoluteUrl(path) },
        ])}
      />
      <RollCallView vote={vote} path={path} />
    </>
  );
}
