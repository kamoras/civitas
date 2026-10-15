import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { pageMetadata } from "@/lib/site";
import ExploreDocumentView from "./ExploreDocumentView";
import { fetchExploreDoc } from "./fetchDoc";

type PageProps = {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ q?: string | string[] }>;
};

/**
 * The document's own metadata: without it every document inherited
 * /explore's title, description and canonical — thousands of pages telling
 * search engines they were one page. Canonical drops `?q=`: the query only
 * says which results the reader came from.
 */
export async function generateMetadata({ params }: PageProps): Promise<Metadata> {
  const { id } = await params;
  const doc = await fetchExploreDoc(id);
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

/** A document's page, its text rendered on the server. A missing document
 * is a real 404 and an outage an error page (AGENTS.md, Search metadata);
 * `?q=` is read here rather than with useSearchParams so the page is never
 * rendered without it. */
export default async function ExploreDocumentPage({ params, searchParams }: PageProps) {
  const { id } = await params;
  const { q } = await searchParams;
  const doc = await fetchExploreDoc(id);
  if (!doc) notFound();
  return <ExploreDocumentView key={doc.id} doc={doc} query={typeof q === "string" ? q : ""} />;
}
