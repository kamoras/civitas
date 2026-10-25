/** GET /api/crawler (backend/app/crawler.py): who the crawler is, what it
 *  reads, how fast and when, read from the backend's own code. Every
 *  figure and phrase is the backend's. */

export interface CrawlerReader {
  /** The module that sends the requests ("app.pipeline.fetch.fec"). */
  module: string;
  /** Its limiter's requests a second; null when it has none of its own. */
  rps: number | null;
  /** The sources registry listing the host, when it is listed there. */
  registry: string | null;
  /** The limit in words ("1 request every 4 seconds at most"). */
  pace: string;
}

export interface CrawlerHost {
  host: string;
  readers: CrawlerReader[];
}

export interface CrawlerFacts {
  userAgent: string;
  page: string;
  contact: string | null;
  signing: { configured: boolean; directory: string | null };
  nightly: { when: string; links: string[] };
  schedule: { job: string; when: string; reads: string }[];
  hosts: CrawlerHost[];
  hostCount: number;
  readerCount: number;
  unlimitedHostCount: number;
}
