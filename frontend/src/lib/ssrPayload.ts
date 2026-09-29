/**
 * The server-rendered detail routes' half of the starved-backend guarantee.
 *
 * `lib/api.ts`'s `asList`/`withShape` make the *client* data layer honest, and
 * the sweep behind them covers the views that go through it. The five SSR
 * detail routes do not: `/politicians/[id]`, `/bills/[id]`, `/issue/[id]`,
 * `/elections/[raceId]` and `/elections/states/[state]` each call `fetch()`
 * directly in the page component and hand the parsed body straight to a client
 * component. Every one of them guards with
 *
 *     if (!res.ok) return null;      // then: if (!payload) notFound()
 *
 * which catches a 500 and catches a `null` body, and does not catch `{}` —
 * because `{}` is truthy. A backend returning an empty object therefore got
 * destructured into a page full of `undefined`, and the first property read
 * threw: `Cannot read properties of undefined (reading 'isCurrent')`. Verified
 * against a stub answering `{}` to everything — all five returned **HTTP 500**,
 * which nginx serves as a server error, not as the "no record" page that
 * already exists two lines below.
 *
 * `{}` is not a hypothetical here. The backend declares a `response_model` on
 * 3 of its 103 routes; `_build_scorecard` is wrapped in a bare
 * `except Exception: return None`; and the pipeline fills this database
 * overnight on a Pi, so "the endpoint exists but has nothing behind it yet" is
 * a normal state rather than an outage.
 *
 * Coercing is the wrong move at this boundary. A list that should have items
 * can render an empty state; a *record page for a record that isn't there*
 * cannot, and `notFound()` is the honest answer — the reader gets the 404 page,
 * which now carries the site's navigation.
 */
export function usableRecord<T>(payload: unknown, ...requiredKeys: (keyof T & string)[]): T | null {
  if (payload === null || typeof payload !== "object" || Array.isArray(payload)) return null;
  const obj = payload as Record<string, unknown>;
  for (const key of requiredKeys) {
    if (obj[key] === undefined || obj[key] === null) return null;
  }
  return payload as T;
}

/**
 * A detail route's record from the backend: the record, or null when there
 * is none (a 404, or a body without the record's shape — see usableRecord),
 * which the route turns into notFound(). Anything else — the backend
 * unreachable, a 5xx — throws, and app/error.tsx says the page could not be
 * loaded. Returning null for an outage too made every member profile, state
 * ballot, issue and document a 404 marked noindex for as long as the backend
 * was down or restarting: a search engine drops a page it is told is gone,
 * and a reader was told a sitting senator doesn't exist. A 5xx is the answer
 * crawlers retry. congressServer.ts made the same change for /congress.
 */
export async function fetchRecord<T>(
  url: string,
  init: RequestInit,
  ...requiredKeys: (keyof T & string)[]
): Promise<T | null> {
  const res = await fetch(url, init);
  if (res.status === 404) return null;
  if (!res.ok) throw new Error(`${url}: HTTP ${res.status}`);
  return usableRecord<T>(await res.json(), ...requiredKeys);
}
