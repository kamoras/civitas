import Navbar from "@/components/layout/Navbar";
import Footer from "@/components/layout/Footer";
import Masthead from "@/components/home/Masthead";
import RecordIndex from "@/components/home/RecordIndex";
import Holdings from "@/components/home/Holdings";
import OnTheRecord from "@/components/home/OnTheRecord";
import ElectionNight from "@/components/home/ElectionNight";
import ThisWeekStrip from "@/components/shared/ThisWeekStrip";
import JsonLd from "@/components/seo/JsonLd";
import { HOME_DESCRIPTION, HOME_TITLE, pageMetadata } from "@/lib/site";
import { siteJsonLd } from "@/lib/seo";

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
export default function Home() {
  return (
    <>
      <JsonLd data={SITE_JSON_LD} />
      <Navbar />
      <main id="main-content" tabIndex={-1} className="pt-[var(--header-clearance)] pb-16">
        {/* Renders only while election results are up (from election night
            until the count settles): the count is the news then. */}
        <ElectionNight />
        <Masthead />
        <OnTheRecord />
        <ThisWeekStrip variant="full" />
        <div className="mx-auto mt-8 grid max-w-7xl grid-cols-1 gap-8 px-4 sm:px-6 md:mt-10 md:grid-cols-12 md:gap-9">
          <RecordIndex />
          <Holdings />
        </div>
      </main>
      <Footer />
    </>
  );
}
