import type { NextRequest } from "next/server";
import { billPageHref } from "@/lib/congress";

/**
 * The header search's target (GET /search?q=). A stand-in until the
 * site-wide results page exists: a bill number ("H.R. 7008", "s 4668")
 * opens that bill's page, which full-text search ranked below documents
 * that merely share the digits; anything else goes to Explore, which reads
 * ?q= on load and runs it. A temporary redirect, so the address can become
 * the results page without anything cached against it.
 */
export function GET(request: NextRequest) {
  const q = (request.nextUrl.searchParams.get("q") ?? "").trim();
  const to = q ? (billPageHref(q) ?? `/explore?q=${encodeURIComponent(q)}`) : "/explore";
  return new Response(null, { status: 307, headers: { Location: to } });
}
