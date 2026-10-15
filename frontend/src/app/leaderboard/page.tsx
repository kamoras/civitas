import { Suspense } from "react";
import PageFallback from "@/components/layout/PageFallback";
import { camelizeKeys } from "@/lib/api";
import type { President } from "@/types/president";
import LeaderboardClient, { type LeaderboardInitial } from "./LeaderboardClient";

const BACKEND = process.env.BACKEND_URL || "http://backend:8000";

// The client's own lifetime for these lists (lib/api TTL.SHORT is a minute).
const REVALIDATE_S = 60;

// Each list as the page's own fetch (lib/api) would hand it over, or null
// when the backend can't answer: the page then asks for it itself.
async function read<T>(
  path: string,
  usable: (body: unknown) => boolean,
  shape: (body: unknown) => unknown = (b) => b
): Promise<T | null> {
  try {
    const res = await fetch(`${BACKEND}/api${path}`, { next: { revalidate: REVALIDATE_S } });
    if (!res.ok) return null;
    const body: unknown = shape(await res.json());
    return usable(body) ? (body as T) : null;
  } catch {
    return null;
  }
}

// The sitting president's record, null when there is none (404), or
// undefined when it couldn't be read (lib/api fetchCurrentPresident).
async function readCurrentPresident(): Promise<President | null | undefined> {
  try {
    const res = await fetch(`${BACKEND}/api/presidents/current`, {
      next: { revalidate: REVALIDATE_S },
    });
    if (res.status === 404) return null;
    return res.ok ? ((await res.json()) as President) : undefined;
  } catch {
    return undefined;
  }
}

const isList = (b: unknown) => Array.isArray(b);
const isPage = (b: unknown) => !!b && Array.isArray((b as { entries?: unknown }).entries);
const isSpread = (b: unknown) => !!b && Array.isArray((b as { counts?: unknown }).counts);

/**
 * Rendered per request with the ranking the URL asks for: the masthead,
 * the field strip and the table are in the HTML. As a prerendered client
 * page it sent a skeleton, and the field strip's line, the largest text in
 * the first screen, painted at 2.1 s on a throttled phone (2026-10-14).
 * Anything the server could not read, the page reads itself, as before.
 */
export default async function LeaderboardPage({
  searchParams,
}: {
  searchParams: Promise<{ branch?: string | string[] }>;
}) {
  const branch = (await searchParams).branch;
  let initial: LeaderboardInitial | null = null;
  if (branch === undefined || branch === "senate") {
    const [senate, spread] = await Promise.all([
      read<LeaderboardInitial["senate"]>("/senators/leaderboard", isList),
      read<LeaderboardInitial["spread"]>("/leaderboard/spread?chamber=senate", isSpread),
    ]);
    initial = { senate, spread };
  } else if (branch === "house") {
    const [house, spread] = await Promise.all([
      read<LeaderboardInitial["house"]>(
        "/representatives/leaderboard?page=1&per_page=50&sort=score&dir=desc",
        isPage
      ),
      read<LeaderboardInitial["spread"]>("/leaderboard/spread?chamber=house", isSpread),
    ]);
    initial = { house, spread };
  } else if (branch === "president") {
    const [presidents, currentPresident] = await Promise.all([
      read<LeaderboardInitial["presidents"]>("/presidents/leaderboard", isList),
      readCurrentPresident(),
    ]);
    initial = { presidents, currentPresident };
  } else if (branch === "scotus") {
    initial = {
      justices: await read<LeaderboardInitial["justices"]>(
        "/justices/leaderboard",
        isList,
        camelizeKeys
      ),
    };
  }
  return (
    <Suspense
      fallback={
        <PageFallback
          eyebrow={"Leaderboard · ranked by representation score"}
          title={"Leaderboard"}
          width="max-w-7xl"
          mastheadClassName="mb-8"
          rows={6}
        />
      }
    >
      <LeaderboardClient initial={initial} />
    </Suspense>
  );
}
