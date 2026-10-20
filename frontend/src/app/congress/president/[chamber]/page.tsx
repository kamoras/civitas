import Link from "next/link";
import { congressOrdinal } from "@/lib/sources";
import { notFound } from "next/navigation";
import { connection } from "next/server";
import Navbar from "@/components/layout/Navbar";
import Footer from "@/components/layout/Footer";
import BackToTop from "@/components/BackToTop";
import PageMasthead from "@/components/layout/PageMasthead";
import SectionTabs from "@/components/layout/SectionTabs";
import ChamberView from "@/components/presidentialSupport/ChamberView";
import { pageMetadata } from "@/lib/site";
import { fetchRecord } from "@/lib/ssrPayload";
import { BOXED_CONTROL } from "@/lib/controlStyles";
import { presidentSupportHref } from "@/lib/presidentialSupport";
import type { ChamberSupport } from "@/types/presidentialSupport";

const BACKEND = process.env.BACKEND_URL || "http://backend:8000";
const CHAMBERS = { house: "House", senate: "Senate" } as const;
type ChamberKey = keyof typeof CHAMBERS;

function chamberOf(raw: string): ChamberKey | null {
  return raw === "house" || raw === "senate" ? raw : null;
}

export async function generateMetadata({ params }: { params: Promise<{ chamber: string }> }) {
  const chamber = chamberOf((await params).chamber);
  if (!chamber)
    return pageMetadata({
      title: "Not found",
      description: "",
      path: "/congress/president",
      noindex: true,
    });
  return pageMetadata({
    title: `How Often Each ${chamber === "house" ? "House Member" : "Senator"} Votes With the President`,
    description: `Every ${CHAMBERS[chamber]} member's votes on the president's side of the bills a Statement of Administration Policy addresses, beside what members of the same party in seats that lean the same way do.`,
    path: presidentSupportHref(chamber),
  });
}

export default async function PresidentSupportPage({
  params,
}: {
  params: Promise<{ chamber: string }>;
}) {
  const chamber = chamberOf((await params).chamber);
  if (!chamber) notFound();
  // Per request: `next build` can't reach the backend. Measured nightly, so
  // the fetch itself is cached for the backend's own two minutes.
  await connection();
  const view = await fetchRecord<ChamberSupport>(
    `${BACKEND}/api/presidential-support/${chamber}`,
    { next: { revalidate: 120 } },
    "members",
    "method"
  );
  return (
    <div className="min-h-screen bg-surface-base font-sans text-ink-hi">
      <Navbar />
      <main id="main-content" tabIndex={-1} className="px-4 pb-16 pt-[var(--header-clearance)]">
        <div className="mx-auto max-w-6xl">
          <SectionTabs section="congress" active="president" />
          <PageMasthead
            eyebrow={`${CHAMBERS[chamber]} · ${view ? `${congressOrdinal(view.congress)} Congress` : "This Congress"}`}
            title="Votes with the president"
            aside={
              <nav aria-label="Chamber" className="grid grid-cols-2">
                {(Object.keys(CHAMBERS) as ChamberKey[]).map((c) => (
                  <Link
                    key={c}
                    href={presidentSupportHref(c)}
                    aria-current={c === chamber ? "page" : undefined}
                    className={`flex min-h-11 items-center justify-center border px-4 font-mono text-sm ${
                      c === chamber ? BOXED_CONTROL.selected : BOXED_CONTROL.unselected
                    }`}
                  >
                    {CHAMBERS[c]}
                  </Link>
                ))}
              </nav>
            }
          >
            <p>
              Before many votes, the White House says where the president stands on the bill. This
              page counts how often each member voted that way, and sets it beside what members of
              the same party in seats that lean the same way do. Voting with or against the
              president is neither good nor bad.
            </p>
          </PageMasthead>
          <div className="mt-8">
            {view ? (
              <ChamberView view={view} />
            ) : (
              <p className="font-sans text-base text-ink-lo">
                Not measured yet: the first nightly run after this page went live fills it in.
              </p>
            )}
          </div>
        </div>
      </main>
      <BackToTop />
      <Footer />
    </div>
  );
}
