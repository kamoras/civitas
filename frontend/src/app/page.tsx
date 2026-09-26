import Navbar from "@/components/layout/Navbar";
import Footer from "@/components/layout/Footer";
import Masthead from "@/components/home/Masthead";
import RecordIndex from "@/components/home/RecordIndex";
import Holdings from "@/components/home/Holdings";
import JsonLd from "@/components/seo/JsonLd";
import { BSKY_PROFILE_URL, GITHUB_REPO_URL, HOME_DESCRIPTION, HOME_TITLE, SITE_NAME, SITE_URL, pageMetadata } from "@/lib/site";

export const metadata = pageMetadata({
  title: HOME_TITLE,
  absoluteTitle: true,
  description: HOME_DESCRIPTION,
  path: "/",
});

// Names the site as an entity, so a search for "Civitas" can resolve to
// this domain rather than to the dictionary word or any other project.
const SITE_JSON_LD = [
  {
    "@context": "https://schema.org",
    "@type": "WebSite",
    name: SITE_NAME,
    alternateName: "Civitas Research",
    url: SITE_URL,
    description: HOME_DESCRIPTION,
    inLanguage: "en-US",
  },
  {
    "@context": "https://schema.org",
    "@type": "Organization",
    name: SITE_NAME,
    url: SITE_URL,
    logo: `${SITE_URL}/icon.svg`,
    sameAs: [BSKY_PROFILE_URL, GITHUB_REPO_URL],
  },
];

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
        <Masthead />
        <div className="mx-auto mt-8 grid max-w-7xl grid-cols-1 gap-8 px-4 sm:px-6 md:mt-10 md:grid-cols-12 md:gap-9">
          <RecordIndex />
          <Holdings />
        </div>
      </main>
      <Footer />
    </>
  );
}
