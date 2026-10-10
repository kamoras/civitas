/** Whether an issue's summary only repeats its title.
 *
 * The summary is the lead source's own lede, and some feeds send their
 * headline as the description too, so an issue page printed the same
 * sentence twice, as the heading and again under it (the homepage list and
 * the Action Center did the same). Compared on letters and digits only, so
 * a trailing period or a curly quote doesn't hide the repeat. */
export function summaryRestatesTitle(title: string, summary: string | null | undefined): boolean {
  const plain = (s: string) => s.toLowerCase().replace(/[^\p{L}\p{N}]+/gu, "");
  return !!summary && plain(summary) === plain(title);
}
