import Navbar from "@/components/layout/Navbar";
import Footer from "@/components/layout/Footer";
import Masthead from "@/components/home/Masthead";
import RecordIndex from "@/components/home/RecordIndex";
import Holdings from "@/components/home/Holdings";

/*
  MatrixRain is deliberately not mounted here any more.

  It was a full-screen requestAnimationFrame canvas behind every page, and on
  the homepage it was the loudest thing on screen — a conspiracy motif in
  front of a project whose entire claim is deterministic, auditable scoring
  from public filings. The terminal identity is carried now by the phosphor
  palette, the mono data face and the records band. The component itself is
  untouched and still mounted on the other pages that use it, so this is one
  page's decision, not a deletion.
*/
export default function Home() {
  return (
    <>
      <Navbar />
      <main id="main-content" tabIndex={-1} className="pt-24 pb-16">
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
