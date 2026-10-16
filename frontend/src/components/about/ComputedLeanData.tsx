import CiteThis from "@/components/shared/CiteThis";
import JsonLd from "@/components/seo/JsonLd";
import { leanWindowWords } from "@/lib/elections";
import { GITHUB_REPO_URL, absoluteUrl } from "@/lib/site";
import { congressOrdinal } from "@/lib/sources";

const BACKEND = process.env.BACKEND_URL || "http://backend:8000";
// The table as data: the public API's JSON (method, inputs and licenses,
// corrections, validation, every district) and the same districts as CSV.
const JSON_PATH = "/api/public/v1/district-lean";
const CSV_PATH = "/api/elections/district-lean.csv";
const LINK = "text-signal-cyan underline underline-offset-2 hover:text-phos";

type LeanTable = {
  asOf: string;
  sha256: string | null;
  /** "2020+2024": the elections the lean is computed from. */
  window: string | null;
  /** The Congresses whose lines the table covers, oldest first. */
  congresses: number[];
};

/** The table's date, version, window and Congresses, for the citation. Null when the backend
 *  can't be reached (at build time, or an outage): the links still work and
 *  the citation reads "n.d." until the next revalidation. */
async function fetchTable(): Promise<LeanTable | null> {
  try {
    const res = await fetch(`${BACKEND}${JSON_PATH}`, { next: { revalidate: 600 } });
    if (!res.ok) return null;
    const body = (await res.json()) as {
      asOf?: unknown;
      sha256?: string | null;
      window?: string | null;
      congresses?: { congress: number }[];
    };
    if (typeof body.asOf !== "string") return null;
    return {
      asOf: body.asOf,
      sha256: body.sha256 ?? null,
      window: body.window || null,
      congresses: (body.congresses ?? []).map((c) => c.congress).filter(Number.isInteger),
    };
  } catch {
    return null;
  }
}

/** Downloads, sources and a citation for the computed district lean. */
export default async function ComputedLeanData() {
  const table = await fetchTable();
  const file = (path: string) => `${GITHUB_REPO_URL}/blob/main/${path}`;
  // Named from the table itself, never a literal: the Congresses and the
  // election window change as the file is rebuilt each cycle.
  const lines = table?.congresses.length
    ? `the ${new Intl.ListFormat("en-US").format(table.congresses.map(congressOrdinal))} Congress lines`
    : "each Congress's lines on file";
  const title = `Computed partisan lean of U.S. House districts, ${lines.replace(/^the /, "")}`;
  const years = leanWindowWords(table?.window);
  return (
    <div className="space-y-3 border border-white/15 p-4 text-base leading-relaxed text-ink">
      <JsonLd
        data={{
          "@context": "https://schema.org",
          "@type": "Dataset",
          name: title,
          description: `Each U.S. House district's partisan lean by Cook's formula, computed from open ${
            years ? `${years} ` : ""
          }precinct returns on ${lines}, with Cook's published PVI beside it.`,
          url: absoluteUrl("/about/data#computed-lean"),
          isAccessibleForFree: true,
          ...(table ? { dateModified: table.asOf } : {}),
          distribution: [
            {
              "@type": "DataDownload",
              encodingFormat: "text/csv",
              contentUrl: absoluteUrl(CSV_PATH),
            },
            {
              "@type": "DataDownload",
              encodingFormat: "application/json",
              contentUrl: absoluteUrl(JSON_PATH),
            },
          ],
        }}
      />
      <p>
        <span className="font-mono text-xs uppercase tracking-[0.12em] text-ink-min">
          Every district, both maps:{" "}
        </span>
        <a href={CSV_PATH} download className={LINK}>
          CSV
        </a>
        {" · "}
        <a href={JSON_PATH} className={LINK}>
          JSON
        </a>{" "}
        <span className="text-ink-lo">
          (the JSON adds the method, each input with its license, every correction with its
          certified source, and the agreement with Cook; it is also the public API&apos;s{" "}
          <code className="font-mono text-sm">/district-lean</code>)
        </span>
      </p>
      <p className="text-ink-lo">
        Built by{" "}
        <a href={file("backend/scripts/build_district_lean.py")} className={LINK}>
          the script
        </a>{" "}
        into{" "}
        <a href={file("backend/app/data/district_lean.json")} className={LINK}>
          the data file
        </a>
        , from the inputs and{" "}
        <a href={file("backend/app/data/district_lean_corrections.json")} className={LINK}>
          the corrections
        </a>{" "}
        listed in it.
        {table?.sha256 && (
          <>
            {" "}
            The file served now has SHA-256{" "}
            <code className="font-mono text-sm break-all">{table.sha256}</code>, which names the
            exact version a citation used.
          </>
        )}
      </p>
      <div>
        <p className="mb-1 font-mono text-xs uppercase tracking-[0.12em] text-ink-min">Cite this</p>
        <CiteThis title={title} date={table?.asOf} path={JSON_PATH} />
      </div>
    </div>
  );
}
