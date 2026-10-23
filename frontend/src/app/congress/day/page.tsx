import { redirect } from "next/navigation";
import { connection } from "next/server";
import { fetchLatestDay } from "@/lib/congressServer";
import { dayHref } from "@/lib/congress";

/** "Latest day" in the Congress section (SECTIONS): the report of the last
 * day either chamber met. Per request: `next build` can't reach the
 * backend. Before any day is recorded, /congress says so. */
export default async function LatestDay() {
  await connection();
  const day = await fetchLatestDay();
  redirect(day ? dayHref(day.date) : "/congress");
}
