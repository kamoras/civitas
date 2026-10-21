import Navbar from "@/components/layout/Navbar";
import Footer from "@/components/layout/Footer";
import Masthead from "@/components/home/Masthead";
import FrontPage from "@/components/home/FrontPage";
import ElectionNight from "@/components/home/ElectionNight";
import HomeTasks from "@/components/home/HomeTasks";
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
        {/* The record first, as a front page (a lead story, a second tier,
            then Congress, Agencies and the news), its nameplate in the
            first screen at 1280x900 and 390x844; then the three tasks a
            visitor comes for, with registration closing soon as one line.
            Where it all comes from is on /about/data; the header menu is
            the one list of the site's pages. */}
        <FrontPage />
        <HomeTasks />
      </main>
      <Footer />
    </>
  );
}
