/** An Explore document's page: its permanent key (backend
 * services/explore_keys.py, from the document's official identifier), or
 * its row id from a backend older than keys, which the page redirects to
 * the key. Link and cite with this, never `/explore/${id}`. */
export function exploreHref(doc: { id: number | string; key?: string | null }): string {
  return `/explore/${encodeURIComponent(doc.key || String(doc.id))}`;
}

/** The official copy a permanent key names, for a key no document answers
 * (explore/not-found.tsx): a Federal Register document number, or a
 * Congressional Record granule. Null for anything else. Mirrors the key
 * formats of backend services/explore_keys.py. */
export function officialSourceForKey(
  key: string
): { identifier: string; label: string; url: string } | null {
  const fr = /^fr-(\d{4}-\d{4,6})$/.exec(key);
  if (fr) {
    return {
      identifier: `FR Doc. ${fr[1]}`,
      label: "the Federal Register",
      url: `https://www.federalregister.gov/d/${fr[1]}`,
    };
  }
  const crec = /^crec-(CREC-(\d{4}-\d{2}-\d{2})-pt\d+-Pg[HSED]\d+(?:-\d+)?)-[0-9a-f]+$/.exec(key);
  if (crec) {
    return {
      identifier: `the Congressional Record granule ${crec[1]}`,
      label: "GovInfo",
      url: `https://www.govinfo.gov/app/details/CREC-${crec[2]}/${crec[1]}`,
    };
  }
  return null;
}
