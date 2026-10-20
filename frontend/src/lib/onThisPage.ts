/** What makes a heading one of the page's sections in a phone's "on this
 *  page" bar (components/shared/OnThisPage.tsx): its label in the bar and the
 *  sheet, and an optional count ("47 recorded", a score). The heading needs
 *  an `id`. Spread it on the heading: `<h2 id="votes" {...onThisPage("Votes", 47)}>`.
 *  A plain module, not the client component's, so server components can
 *  mark their headings too. */
export function onThisPage(label: string, count?: string | number) {
  return {
    "data-section": label,
    ...(count !== undefined && count !== "" ? { "data-section-count": String(count) } : {}),
  };
}
