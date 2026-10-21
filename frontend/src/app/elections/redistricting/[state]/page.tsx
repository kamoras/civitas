import Link from "next/link";
import { Metadata } from "next";
import { congressOrdinal } from "@/lib/sources";
import { notFound } from "next/navigation";
import type { StateRedistricting } from "@/types/redistricting";
import { fetchRecord } from "@/lib/ssrPayload";
import { absoluteUrl, notFoundMetadata, pageMetadata } from "@/lib/site";
import { CARD_ALT, cardImage, describeStateRedistricting } from "@/lib/seo";
import { REDISTRICTING_HREF, redistrictingHref } from "@/lib/redistricting";
import JsonLd, { breadcrumbList } from "@/components/seo/JsonLd";
import SectionTabs from "@/components/layout/SectionTabs";
import Navbar from "@/components/layout/Navbar";
import Footer from "@/components/layout/Footer";
import PageMasthead from "@/components/layout/PageMasthead";
import RedistrictingStateView from "@/components/redistricting/RedistrictingView";

const BACKEND = process.env.BACKEND_URL || "http://backend:8000";

// The figures change only when a map or a pinned lean table does.
const REVALIDATE_S = 3600;

/** Null for a state that kept its lines; an outage throws (fetchRecord). */
function fetchState(state: string): Promise<StateRedistricting | null> {
  return fetchRecord<StateRedistricting>(
    `${BACKEND}/api/elections/redistricting/${encodeURIComponent(state)}`,
    { next: { revalidate: REVALIDATE_S } },
    "state",
    "newDistricts",
    "oldDistricts",
    "summary"
  );
}

export async function generateMetadata({
  params,
}: {
  params: Promise<{ state: string }>;
}): Promise<Metadata> {
  const { state } = await params;
  const code = state.toUpperCase();
  const data = await fetchState(code);
  const path = redistrictingHref(code);
  if (!data) {
    return notFoundMetadata(
      "No redrawn map for this state",
      `${code} did not redraw its U.S. House map for this election.`
    );
  }
  return pageMetadata({
    ...describeStateRedistricting(
      data.stateName,
      data.cycle,
      data.summary.seats,
      data.summary.residentsMovedPct
    ),
    path,
    type: "article",
    images: cardImage(`redistricting=${code}`, CARD_ALT.redistricting(data.stateName)),
  });
}

export default async function StateRedistrictingPage({
  params,
}: {
  params: Promise<{ state: string }>;
}) {
  const { state } = await params;
  const data = await fetchState(state.toUpperCase());
  if (!data) notFound();

  return (
    <div className="min-h-screen bg-surface-base font-sans text-ink-hi">
      <JsonLd
        data={breadcrumbList([
          { name: "Home", url: absoluteUrl("/") },
          { name: "Elections", url: absoluteUrl("/elections") },
          { name: "New House maps", url: absoluteUrl(REDISTRICTING_HREF) },
          { name: data.stateName, url: absoluteUrl(redistrictingHref(data.state)) },
        ])}
      />
      <JsonLd
        data={{
          "@context": "https://schema.org",
          "@type": "Dataset",
          name: `${data.stateName} U.S. House districts, ${congressOrdinal(data.fromCongress)} to ${congressOrdinal(data.toCongress)} Congress lines: Census resident overlap`,
          description: `Census residents in each pair of an old and a new ${data.stateName} congressional district, with each district's Cook PVI on both maps.`,
          url: absoluteUrl(redistrictingHref(data.state)),
          isAccessibleForFree: true,
          distribution: [
            {
              "@type": "DataDownload",
              encodingFormat: "text/csv",
              contentUrl: absoluteUrl(`/api/elections/redistricting/${data.state}/overlap.csv`),
            },
            {
              "@type": "DataDownload",
              encodingFormat: "application/json",
              contentUrl: absoluteUrl(`/api/elections/redistricting/${data.state}`),
            },
          ],
        }}
      />
      <Navbar />
      <main id="main-content" tabIndex={-1} className="px-4 pb-16 pt-[var(--header-clearance)]">
        <div className="mx-auto max-w-4xl">
          <SectionTabs section="elections" active="redistricting" className="mb-4" />
          {/* Under 640px the section row above names where this page sits. */}
          <nav aria-label="Breadcrumb" className="hidden sm:block mb-6 text-sm text-ink-lo">
            <Link href="/elections" className="inline-flex min-h-6 items-center hover:text-phos">
              Elections
            </Link>
            {" / "}
            <Link
              href={REDISTRICTING_HREF}
              className="inline-flex min-h-6 items-center hover:text-phos"
            >
              New House maps
            </Link>
            {" / "}
            <span aria-current="page">{data.stateName}</span>
          </nav>
          <PageMasthead
            eyebrow={`New House maps · ${data.cycle} · ${data.state}`}
            title={`${data.stateName}'s new map`}
          />
          <RedistrictingStateView data={data} />
        </div>
      </main>
      <Footer />
    </div>
  );
}
