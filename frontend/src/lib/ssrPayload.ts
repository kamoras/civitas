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
