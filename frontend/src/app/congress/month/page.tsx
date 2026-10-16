import { redirect } from "next/navigation";
import { connection } from "next/server";
import { fetchLatestDay } from "@/lib/congressServer";
import { monthHref } from "@/lib/congress";

/** "Latest month" in the Congress section (SECTIONS): the report of the
 * last month either chamber met. See app/congress/week/page.tsx. */
export default async function LatestMonth() {
  await connection();
  const day = await fetchLatestDay();
  redirect(day ? monthHref(day.date) : "/congress");
}
