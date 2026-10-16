import Link from "next/link";
import { connection } from "next/server";
import type { ElectionMapData } from "@/types/electionMap";
import { absoluteUrl, pageMetadata } from "@/lib/site";
import { describeElectionMap } from "@/lib/seo";
import { ELECTION_MAP_HREF, VIEW_LABEL, formatAsOf, parseView } from "@/lib/electionMap";
import Navbar from "@/components/layout/Navbar";
import Footer from "@/components/layout/Footer";
import PageMasthead from "@/components/layout/PageMasthead";
import ElectionMap from "@/components/elections/map/ElectionMap";
import ElectionMapMethod from "@/components/elections/map/ElectionMapMethod";
import { ShareSubjectProvider } from "@/components/share/ShareSubjectContext";

const BACKEND = process.env.BACKEND_URL || "http://backend:8000";

type MapState =
  | { kind: "map"; data: ElectionMapData }
  | { kind: "hidden"; detail: string }
  | { kind: "not_built"; detail: string };

/** The map's data; "hidden" from election day on (the backend answers
 * 404), "not_built" before the pipeline first writes it (503). Anything
 * else — the backend unreachable, another error — throws to error.tsx. */
async function fetchMap(): Promise<MapState> {
  const res = await fetch(`${BACKEND}/api/elections/map`, { next: { revalidate: 300 } });
  if (res.status === 404 || res.status === 503) {
    const body = (await res.json().catch(() => ({}))) as { detail?: string };
    return { kind: res.status === 404 ? "hidden" : "not_built", detail: body.detail ?? "" };
  }
  if (!res.ok) throw new Error(`election map: HTTP ${res.status}`);
  return { kind: "map", data: (await res.json()) as ElectionMapData };
}

export async function generateMetadata() {
  const state = await fetchMap().catch(() => null);
  return pageMetadata({
    ...describeElectionMap(state?.kind === "map" ? state.data : null),
    path: ELECTION_MAP_HREF.split("?")[0],
    noindex: state?.kind === "hidden",
  });
}

export default async function ElectionMapPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  // Rendered per request: the backend isn't reachable under `next build`,
  // and the view comes from the query string, so a shared link's HTML is
  // already drawn in the view it names.
  await connection();
  const [state, params] = await Promise.all([fetchMap(), searchParams]);
  const view = parseView(params.view);
  return (
    <div className="min-h-screen bg-surface-base font-sans text-ink-hi">
      <Navbar />
      <main id="main-content" tabIndex={-1} className="px-4 pb-16 pt-[var(--header-clearance)]">
        <div className="mx-auto max-w-6xl space-y-8">
          <PageMasthead eyebrow="2026 general election · Senate" title="Where seats lean">
            How each Senate seat usually votes, and the range this year&apos;s polls suggest for the
            margin. Ranges, not results. Not a race call.
          </PageMasthead>
          {state.kind === "map" ? (
            <ShareSubjectProvider
              subject={{
                title: `2026 Senate map, ${VIEW_LABEL[view].toLowerCase()}`,
                subtitle: `As of ${formatAsOf(state.data.asOf)} · Ranges, not results · Not a race call`,
                url: absoluteUrl(ELECTION_MAP_HREF.replace("view=usual", `view=${view}`)),
                source:
                  "Civitas, from official returns (MEDSL, FEC); polls: Wikipedia contributors, CC BY-SA 4.0",
                asOf: state.data.asOf,
              }}
            >
              <ElectionMap
                data={state.data}
                initialView={view}
                initialSeat={typeof params.seat === "string" ? params.seat : null}
              />
              <div className="mt-12 max-w-3xl">
                <ElectionMapMethod data={state.data} />
              </div>
            </ShareSubjectProvider>
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
