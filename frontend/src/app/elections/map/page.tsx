import Link from "next/link";
import { connection } from "next/server";
import type { ElectionMapResponse } from "@/types/electionMap";
import { absoluteUrl, pageMetadata } from "@/lib/site";
import { describeElectionMap } from "@/lib/seo";
import { ELECTION_MAP_HREF, parseChamber, parseView, type MapChamber } from "@/lib/electionMap";
import Navbar from "@/components/layout/Navbar";
import Footer from "@/components/layout/Footer";
import PageMasthead from "@/components/layout/PageMasthead";
import ElectionMap from "@/components/elections/map/ElectionMap";

const BACKEND = process.env.BACKEND_URL || "http://backend:8000";
const PATH = ELECTION_MAP_HREF.split("?")[0];

type MapState =
  | { kind: "map"; data: ElectionMapResponse }
  | { kind: "hidden"; detail: string }
  | { kind: "not_built"; detail: string };

/** The map's data; "hidden" from election day on (the backend answers
 * 404), "not_built" before the pipeline first writes it (503). Anything
 * else — the backend unreachable, another error — throws to error.tsx. */
async function fetchMap(chamber: MapChamber = "senate"): Promise<MapState> {
  // One chamber: the other is fetched by the page when a reader asks for it.
  const res = await fetch(`${BACKEND}/api/elections/map?chamber=${chamber}`, {
    next: { revalidate: 300 },
  });
  if (res.status === 404 || res.status === 503) {
    const body = (await res.json().catch(() => ({}))) as { detail?: string };
    return { kind: res.status === 404 ? "hidden" : "not_built", detail: body.detail ?? "" };
  }
  if (!res.ok) throw new Error(`election map: HTTP ${res.status}`);
  return { kind: "map", data: (await res.json()) as ElectionMapResponse };
}

export async function generateMetadata() {
  const state = await fetchMap().catch(() => null);
  return pageMetadata({
    ...describeElectionMap(state?.kind === "map" ? state.data.cycle : undefined),
    path: PATH,
    noindex: state?.kind === "hidden",
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
          <PageMasthead
            eyebrow={`${state.kind === "map" ? `${state.data.cycle} general` : "General"} election · Senate and House`}
            title="Where seats lean"
          >
            How each seat usually votes, and the range this year&apos;s polls suggest for the
            margin. Ranges, not results. Not a race call.
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
              <p>
                <Link href="/elections" className="text-ink-hi underline">
                  Elections →
                </Link>
              </p>
            </div>
          )}
        </div>
      </main>
      <Footer />
    </div>
  );
}
