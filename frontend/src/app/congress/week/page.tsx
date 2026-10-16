import { redirect } from "next/navigation";
import { connection } from "next/server";
import { fetchLatestDay } from "@/lib/congressServer";
import { weekHref } from "@/lib/congress";

/** "Latest week" in the Congress section (SECTIONS): the report of the last
 * week either chamber met, so a recess never opens on an empty week. Per
 * request: `next build` can't reach the backend. Before any day is
 * recorded, /congress says so. */
export default async function LatestWeek() {
  await connection();
  const day = await fetchLatestDay();
  redirect(day ? weekHref(day.date) : "/congress");
}
