import Link from "next/link";
import { connection } from "next/server";
import type { ElectionMapResponse } from "@/types/electionMap";
import { absoluteUrl, pageMetadata } from "@/lib/site";
import { describeElectionMap } from "@/lib/seo";
import { ELECTION_MAP_HREF, parseChamber, parseView, type MapChamber } from "@/lib/electionMap";
import SectionTabs from "@/components/layout/SectionTabs";
import { ELECTIONS_ARCHIVE_HREF } from "@/lib/routes";
import Navbar from "@/components/layout/Navbar";
import Footer from "@/components/layout/Footer";
import PageMasthead from "@/components/layout/PageMasthead";
import ElectionMap from "@/components/elections/map/ElectionMap";

const BACKEND = process.env.BACKEND_URL || "http://backend:8000";
const PATH = ELECTION_MAP_HREF.split("?")[0];

type MapState =
  | { kind: "map"; data: ElectionMapResponse }
  | { kind: "hidden"; detail: string }
  | { kind: "not_built"; detail: string }
  | { kind: "unavailable"; detail: string };

/** Said when the backend can't be reached or answers with an error: an
 * outage, worded as one, never as a map that doesn't exist. */
const UNAVAILABLE =
  "The map could not be reached just now. This is an outage on this site, not a missing map, and it is usually over within a minute or two.";

/** The map's data; "hidden" from election day on (the backend answers
 * 404), "not_built" before the pipeline first writes it (503), and
 * "unavailable" when the backend is unreachable or answers any other error.
 * A section page, not a record (fetchRecord): its tabs and links still
 * help, so an outage is said inside the page rather than thrown to
 * error.tsx as a 500. */
async function fetchMap(chamber: MapChamber = "senate"): Promise<MapState> {
  // One chamber: the other is fetched by the page when a reader asks for it.
  try {
    const res = await fetch(`${BACKEND}/api/elections/map?chamber=${chamber}`, {
      next: { revalidate: 300 },
    });
    if (res.status === 404 || res.status === 503) {
      const body = (await res.json().catch(() => ({}))) as { detail?: string };
      return { kind: res.status === 404 ? "hidden" : "not_built", detail: body.detail ?? "" };
    }
    if (res.ok) return { kind: "map", data: (await res.json()) as ElectionMapResponse };
  } catch {
    // Unreachable, or a body that isn't JSON: the same outage.
  }
  return { kind: "unavailable", detail: UNAVAILABLE };
}

export async function generateMetadata() {
  const state = await fetchMap();
  return pageMetadata({
    ...describeElectionMap(state.kind === "map" ? state.data.cycle : undefined),
    path: PATH,
    // Not an outage: a crawler that meets one shouldn't drop the page.
    noindex: state.kind === "hidden",
  });
}

export default async function ElectionMapPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  // Rendered per request: the backend isn't reachable under `next build`,
  // and the view and chamber come from the query string, so a shared
  // link's HTML is already drawn the way it names.
  await connection();
  const params = await searchParams;
  const state = await fetchMap(parseChamber(params.chamber));
  return (
    <div className="min-h-screen bg-surface-base font-sans text-ink-hi">
      <Navbar />
      <main id="main-content" tabIndex={-1} className="px-4 pb-16 pt-[var(--header-clearance)]">
        <div className="mx-auto max-w-6xl space-y-8">
          <SectionTabs section="elections" active="map" className="" />
          <PageMasthead
            eyebrow={`${state.kind === "map" ? `${state.data.cycle} general` : "General"} election · Senate and House`}
            title="Where seats lean"
          >
            How each seat usually votes, and a range for its margin, from this year&apos;s polls
            where a seat has any. Ranges, not results. Not a race call.
          </PageMasthead>
          {state.kind === "map" ? (
            <ElectionMap
              response={state.data}
              initialView={parseView(params.view)}
              initialChamber={parseChamber(params.chamber)}
              initialSeat={typeof params.seat === "string" ? params.seat : null}
              pageUrl={absoluteUrl(PATH)}
            />
          ) : (
            <div className="max-w-2xl space-y-3 text-ink-lo">
              <p>{state.detail}</p>
              <p className="flex flex-wrap gap-x-6">
                <Link href="/elections" className="text-ink-hi underline">
                  Elections →
                </Link>
                {state.kind === "hidden" && (
                  <Link href={ELECTIONS_ARCHIVE_HREF} className="text-ink-hi underline">
                    The map before election day, in the archive →
                  </Link>
                )}
              </p>
            </div>
          )}
        </div>
      </main>
      <Footer />
    </div>
  );
}
