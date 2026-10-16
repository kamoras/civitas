import Navbar from "@/components/layout/Navbar";
import Footer from "@/components/layout/Footer";
import Masthead from "@/components/home/Masthead";
import RecordIndex from "@/components/home/RecordIndex";
import Holdings from "@/components/home/Holdings";
import OnTheRecord from "@/components/home/OnTheRecord";
import SectionsBand from "@/components/home/SectionsBand";
import ElectionNight from "@/components/home/ElectionNight";
import ThisWeekStrip from "@/components/shared/ThisWeekStrip";
import JsonLd from "@/components/seo/JsonLd";
import { HOME_DESCRIPTION, HOME_TITLE, pageMetadata } from "@/lib/site";
import { siteJsonLd } from "@/lib/seo";
import { fetchResultsSummary } from "@/lib/resultsSummaryServer";

export const metadata = pageMetadata({
  title: HOME_TITLE,
  absoluteTitle: true,
  description: HOME_DESCRIPTION,
  path: "/",
});

// Names the site as an entity, so a search for "Civitas Research" resolves
// to this domain rather than to any other Civitas (lib/seo.ts).
const SITE_JSON_LD = siteJsonLd();

/*
  The full-screen canvas animation that used to sit behind this page is gone,
  along with the component itself — it was a conspiracy motif in front of a
  project whose entire claim is deterministic, auditable scoring from public
  filings. The terminal identity is carried now by the phosphor palette, the
  mono data face and the records band.
*/
export default async function Home() {
  // The election's phase, for the masthead's voter links: the same request
  // ElectionNight makes (memoized within the render).
  const summary = await fetchResultsSummary();
  return (
    <>
      <JsonLd data={SITE_JSON_LD} />
      <Navbar />
      <main id="main-content" tabIndex={-1} className="pt-[var(--header-clearance)] pb-16">
        {/* Renders only while election results are up (from election night
            until the count settles): the count is the news then. */}
        <ElectionNight />
        <Masthead electionPhase={summary?.phase.phase ?? null} />
        {/* Ordered by how often each block changes: the day's record (its
            first row the day's top issue), this week, the week's notable
            votes; then the one index of every way into the site, beside
            the sources it is all built from. */}
        <RecordIndex />
        <ThisWeekStrip variant="full" />
        <OnTheRecord />
        <div className="mx-auto mt-10 grid max-w-7xl grid-cols-1 gap-8 px-4 sm:px-6 md:grid-cols-[minmax(0,2fr)_minmax(0,1fr)] md:gap-9">
          <SectionsBand />
          <Holdings />
        </div>
      </main>
      <Footer />
    </>
  );
}
