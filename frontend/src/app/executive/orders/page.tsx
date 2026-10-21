import { redirect } from "next/navigation";
import { connection } from "next/server";
import { fetchSittingPresident } from "@/lib/presidentServer";
import { documentsHref, SEARCH_DOCUMENTS_HREF } from "@/lib/routes";

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
  redirect(president ? documentsHref({ politician_id: president.id }) : SEARCH_DOCUMENTS_HREF);
}
