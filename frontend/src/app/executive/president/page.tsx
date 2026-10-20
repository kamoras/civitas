import { redirect } from "next/navigation";
import { connection } from "next/server";
import { fetchSittingPresident } from "@/lib/presidentServer";
import { PRESIDENTS_HREF } from "@/lib/routes";

/**
 * "The President" in the Executive menu: the sitting president's record,
 * looked up on each request, so the link never names a person and follows
 * an inauguration with no change here. A temporary redirect (307), since
 * where it leads changes with the office; with no president on record it
 * leads to every president.
 */
export default async function SittingPresidentPage() {
  // Per request: a build has no backend to ask, and would bake in its answer.
  await connection();
  const president = await fetchSittingPresident();
  redirect(president ? `/politicians/${encodeURIComponent(president.id)}` : PRESIDENTS_HREF);
}
