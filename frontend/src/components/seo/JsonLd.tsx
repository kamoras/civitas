/**
 * schema.org structured data, rendered as a JSON-LD script tag.
 *
 * Every value here can carry text from outside (bill titles, issue
 * headlines, member names), and JSON.stringify does not escape `<` — a
 * title containing `</script>` would close the tag and inject markup. The
 * `<` escape is still valid JSON, so parsers read the same string.
 */
export default function JsonLd({ data }: { data: Record<string, unknown> | Record<string, unknown>[] }) {
  return (
    <script
      type="application/ld+json"
      dangerouslySetInnerHTML={{ __html: serializeJsonLd(data) }}
    />
  );
}

export function serializeJsonLd(data: unknown): string {
  return JSON.stringify(data).replace(/</g, "\\u003c");
}

/** BreadcrumbList from (name, absolute url) pairs, in order. */
export function breadcrumbList(items: { name: string; url: string }[]) {
  return {
    "@context": "https://schema.org",
    "@type": "BreadcrumbList",
    itemListElement: items.map((item, i) => ({
      "@type": "ListItem",
      position: i + 1,
      name: item.name,
      item: item.url,
    })),
  };
}
