import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { fetchRecord } from "@/lib/ssrPayload";
import { pageMetadata } from "@/lib/site";

const BACKEND = process.env.BACKEND_URL || "http://backend:8000";

interface ExploreDocMeta {
  id: number;
  title: string;
  summary: string;
  docType: string;
  date: string;
  politicianName: string;
  agencyName: string;
}

/** Null only when there is no such document; an outage throws (fetchRecord). */
async function fetchDoc(id: string): Promise<ExploreDocMeta | null> {
  if (!/^\d+$/.test(id)) return null;
  return fetchRecord<ExploreDocMeta>(
    `${BACKEND}/api/explore/${id}`,
    { next: { revalidate: 3600 } },
    "id",
    "title"
  );
}

/**
 * The document page itself is a client component (it streams its summary),
 * so its metadata lives here. Without this layout every document inherited
 * /explore's title, description and canonical — thousands of pages telling
 * search engines they were one page.
 *
 * Canonical drops `?q=`: the query only highlights terms in the same
 * document.
 */
export async function generateMetadata({
  params,
}: {
  params: Promise<{ id: string }>;
}): Promise<Metadata> {
  const { id } = await params;
  const doc = await fetchDoc(id);
  const path = `/explore/${encodeURIComponent(id)}`;

  if (!doc) {
    return pageMetadata({
      title: "Document not found",
      description: "No record for this document.",
      path,
      noindex: true,
    });
  }

  const who = doc.politicianName || doc.agencyName;
  const byline = [doc.docType, who, doc.date].filter(Boolean).join(" · ");
  return pageMetadata({
    title: doc.title,
    description: doc.summary
      ? `${byline}. ${doc.summary}`
      : `${byline}. Full text and plain-language summary on Civitas.`,
    path,
    type: "article",
  });
}

/** A missing document is a real 404 (AGENTS.md, Search metadata): the page
 * is a client component, so the check lives here. The fetch is the one
 * generateMetadata already made, served from Next's fetch cache. */
export default async function ExploreDocumentLayout({
  children,
  params,
}: {
  children: React.ReactNode;
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  if (!(await fetchDoc(id))) notFound();
  return <>{children}</>;
}
