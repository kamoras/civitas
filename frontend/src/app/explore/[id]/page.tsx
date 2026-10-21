import type { Metadata } from "next";
import { notFound, permanentRedirect } from "next/navigation";
import { absoluteUrl, notFoundMetadata, pageMetadata } from "@/lib/site";
import { CARD_ALT } from "@/lib/seo";
import ExploreDocumentView from "./ExploreDocumentView";
import { fetchExploreDoc } from "./fetchDoc";
import { exploreHref } from "@/lib/exploreHref";

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

  if (!doc) {
    return notFoundMetadata("Document not found", "No record for this document.");
  }

  // The canonical is the permanent key, whatever address was asked for.
  const path = exploreHref(doc);
  const who = doc.politicianName || doc.agencyName;
  const byline = [doc.docType, who, doc.date].filter(Boolean).join(" · ");
  return pageMetadata({
    title: doc.title,
    description: doc.summary
      ? `${byline}. ${doc.summary}`
      : `${byline}. Full text and plain-language summary on Civitas.`,
    path,
    type: "article",
    images: [
      {
        url: absoluteUrl(`/api/og?explore=${doc.id}`),
        width: 1200,
        height: 630,
        alt: CARD_ALT.document(doc.title),
      },
    ],
  });
}

/** A document's page, its text rendered on the server. A missing document
 * is a real 404 and an outage an error page (AGENTS.md, Search metadata);
 * `?q=` is read here rather than with useSearchParams so the page is never
 * rendered without it. Any address but the document's permanent key (its
 * row id, from a link made before keys; a speech's copy the pipeline
 * dropped) answers a permanent redirect (308) to the key, `?q=` kept. */
export default async function ExploreDocumentPage({ params, searchParams }: PageProps) {
  const { id } = await params;
  const { q } = await searchParams;
  const doc = await fetchExploreDoc(id);
  if (!doc) notFound();
  if (doc.key && doc.key !== id) {
    permanentRedirect(
      `${exploreHref(doc)}${typeof q === "string" && q ? `?q=${encodeURIComponent(q)}` : ""}`
    );
  }
  return <ExploreDocumentView key={doc.id} doc={doc} query={typeof q === "string" ? q : ""} />;
}
