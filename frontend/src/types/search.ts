/** One match from site search (backend app/site_search.py). */
export interface SearchItem {
  kind: string;
  /** The group heading the kind is listed under ("Members of Congress"). */
  group: string;
  label: string;
  context: string;
  url: string;
}

/** GET /api/search/suggest */
export interface SearchSuggestions {
  query: string;
  /** The page a search for exactly this goes straight to, or null. */
  direct: string | null;
  items: SearchItem[];
  /** True before the index has been built: say so, never "no results". */
  unavailable?: boolean;
}

/** A kind's count in the results, for its filter chip. */
export interface SearchKind {
  kind: string;
  label: string;
  count: number;
}

/** GET /api/search: one list, members, bills, agencies, pages and the
 *  rest ranked together with Explore's documents (backend
 *  app/site_search.fuse). */
export interface SearchResults {
  query: string;
  direct: string | null;
  items: SearchItem[];
  kinds: SearchKind[];
  /** The documents' half didn't answer: the list is matches only. */
  documentsUnavailable?: boolean;
  /** Documents found by their words alone (the meaning index rebuilding). */
  documentsPartial?: boolean;
  /** Close calls beside the top match ("did you mean"). */
  alternatives: SearchItem[];
  /** The closest names by spelling, when nothing matched. */
  near: SearchItem[];
  unavailable?: boolean;
}
