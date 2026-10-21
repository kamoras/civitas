/** Site search's Documents filter (components/search/DocumentResults.tsx):
 * its parameters and Explore's request for them. */

/** The Documents filter's parameters, as the address carries them (and as
 * Explore's page read them before it redirected here). */
export interface DocumentQuery {
  q: string;
  chamber: string;
  type: string;
  agency: string;
  politicianId: string;
  commentOpen: boolean;
  sort: "relevance" | "date";
}

// Which branch's documents: the backend's chamber values.
export const BRANCH_FILTERS: { label: string; value: string }[] = [
  { label: "All", value: "" },
  { label: "Senate", value: "Senate" },
  { label: "House", value: "House" },
  { label: "Executive", value: "Executive" },
  { label: "Supreme Court", value: "Judicial" },
  { label: "Rulemaking", value: "Regulatory" },
];

/** As many results as Explore listed. */
export const DOCUMENT_LIMIT = 30;

/** Whether the parameters ask for a list at all: words, or a member's,
 * an agency's or every open comment period's documents. */
export function asksForDocuments(d: DocumentQuery): boolean {
  return d.q.length >= 2 || !!d.politicianId || !!d.agency || d.commentOpen;
}

/** Explore's own request for these parameters (GET /api/explore): the
 * hybrid ranking of semantic, keyword, recency and citation authority
 * (backend services/explore_search.hybrid_search). With no words, a
 * member's or agency's documents newest first, or every open comment
 * period. */
export function exploreParams(d: DocumentQuery): URLSearchParams {
  const p = new URLSearchParams();
  if (d.q.length >= 2) p.set("q", d.q);
  if (d.type) p.set("doc_type", d.type);
  if (d.chamber) p.set("chamber", d.chamber);
  if (d.commentOpen) p.set("commentable", "true");
  if (d.politicianId) p.set("politician_id", d.politicianId);
  if (d.agency) p.set("agency", d.agency);
  p.set("sort", d.sort);
  p.set("limit", String(DOCUMENT_LIMIT));
  return p;
}

type Params = Record<string, string | string[] | undefined>;

function one(v: string | string[] | undefined, max = 300): string {
  return ((Array.isArray(v) ? v[0] : v) ?? "").trim().slice(0, max);
}

/** The parameters from the address. With no words, a member's or an
 * agency's documents are listed newest first, so that is the default sort
 * there; relevance otherwise. */
export function documentQuery(params: Params): DocumentQuery {
  const q = one(params.q);
  const politicianId = one(params.politician_id, 120);
  const agency = one(params.agency, 200);
  const sortParam = one(params.sort);
  const sort =
    sortParam === "date" || sortParam === "relevance"
      ? sortParam
      : !q && (politicianId || agency)
        ? "date"
        : "relevance";
  const chamber = one(params.chamber, 40);
  return {
    q,
    chamber: BRANCH_FILTERS.some((b) => b.value === chamber) ? chamber : "",
    type: one(params.type, 80),
    agency,
    politicianId,
    commentOpen: one(params.comment) === "open",
    sort,
  };
}
