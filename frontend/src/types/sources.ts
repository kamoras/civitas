/** GET /api/sources (backend/app/api/sources.py): each source the record is
 *  built from, what it has supplied and how recent it is. Every figure and
 *  phrase is the backend's. */

export interface SourceRow {
  label: string;
  source: string;
  count: number;
  /** "1,591 roll calls, 119th Congress". */
  coverage: string;
  /** YYYY-MM-DD, or null when the source has nothing stored. */
  latest: string | null;
  /** What `latest` is: "newest record", or "last read" for a source whose
   *  records carry no date the site keeps. */
  dateLabel: string;
}
