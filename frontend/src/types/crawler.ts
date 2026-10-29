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
  /** The least time between two requests to it ("1 second"). */
  minimumGap: string;
}

export interface CrawlerFacts {
  userAgent: string;
  /** The hosts that refuse userAgent, each with the form it is sent and the measurement. */
  userAgentExceptions: { host: string; userAgent: string; reason: string }[];
  /** Hosts whose own terms forbid automated access: never asked. */
  termsHosts: {
    host: string;
    publisher: string;
    termsUrl: string;
    quote: string;
    readOn: string;
    /** Who asked Civitas not to read the site, and when. */
    asked: string;
    /** When the weekly check last found the terms as reviewed (an Internet Archive capture date). */
    confirmedOn: string;
  }[];
  page: string;
  contact: string | null;
  signing: { configured: boolean; directory: string | null };
  nightly: { when: string; links: string[] };
  schedule: { job: string; when: string; reads: string }[];
  hosts: CrawlerHost[];
  hostCount: number;
  readerCount: number;
  unlimitedHostCount: number;
  /** How the crawler behaves (app/crawl_policy.py), each a worded span. */
  manners: {
    minimumGap: string;
    longestWait: string;
    firstBackoff: string;
    longestBackoff: string;
    robotsKept: string;
  };
  /** Hosts refusing the crawler now, from the fetchers' record. */
  refusing: { host: string; reason: string; since: string; times: number; until: string }[];
}
