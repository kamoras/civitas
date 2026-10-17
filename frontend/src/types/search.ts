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

export interface SearchGroup {
  kind: string;
  label: string;
  count: number;
  items: SearchItem[];
}

/** GET /api/search */
export interface SearchResults {
  query: string;
  direct: string | null;
  groups: SearchGroup[];
  explore: { count: number | null; url: string };
  /** Close calls beside the top match ("did you mean"). */
  alternatives: SearchItem[];
  /** The closest names by spelling, when nothing matched. */
  near: SearchItem[];
  unavailable?: boolean;
}
