import { redirect } from "next/navigation";
import { connection } from "next/server";
import { fetchSittingPresident } from "@/lib/presidentServer";

/**
 * "Executive orders" in the Executive menu: the sitting president's
 * documents in the documents search (executive orders, proclamations and
 * memoranda from the Federal Register), newest first. Resolved on each
 * request like "The President" (app/executive/president); with no
 * president on record, the documents search itself.
 */
export default async function ExecutiveOrdersPage() {
  await connection();
  const president = await fetchSittingPresident();
  redirect(president ? `/explore?politician_id=${encodeURIComponent(president.id)}` : "/explore");
}
