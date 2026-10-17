import type { CitedSource } from "@/components/shared/CiteThis";

/**
 * Who issued a government document or record, and where it was published,
 * for "Cite this" (CiteThis's `source`): the record is theirs, and Civitas
 * is only where it was accessed. Each comes from the record's own fields;
 * a record with no official copy to point at returns null and is cited as
 * the Civitas page it is.
 */

/** "91 FR 54280" -> "Federal Register, 91, 54280", with the FR Doc. number. */
function federalRegister(frCitation?: string, frDocNumber?: string): string {
  const m = /^(\d+) FR (\d+)$/.exec(frCitation ?? "");
  const where = m ? `Federal Register, ${m[1]}, ${m[2]}` : "Federal Register";
  return frDocNumber ? `${where} (FR Doc. ${frDocNumber})` : where;
}

/** "172 Cong. Rec. H5836" -> "Congressional Record, 172, H5836 (daily ed.)". */
function congressionalRecord(crCitation?: string): string {
  const m = /^(\d+) Cong\. Rec\. (\w+)$/.exec(crCitation ?? "");
  return m
    ? `Congressional Record, ${m[1]}, ${m[2]} (daily ed.)`
    : "Congressional Record (daily ed.)";
}

export interface CitableExploreDoc {
  docType: string;
  source: string;
  chamber: string;
  agencyName: string;
  politicianName: string;
  speakerName?: string;
  frCitation?: string;
  frDocNumber?: string;
  crCitation?: string;
}

/** An Explore document: a rule or notice is its agency's, in the Federal
 *  Register; a presidential document the Executive Office of the President's; a floor speech its
 *  speaker's, in the Congressional Record; an opinion the Court's. */
export function exploreCitedSource(
  doc: CitableExploreDoc,
  officialUrl: string
): CitedSource | null {
  const fr = doc.source === "Federal Register" || Boolean(doc.frCitation || doc.frDocNumber);
  const url =
    officialUrl || (doc.frDocNumber ? `https://www.federalregister.gov/d/${doc.frDocNumber}` : "");
  if (!url) return null;
  if (fr) {
    // A presidential document is the Executive Office of the President's,
    // the issuing office, as the Register lists it.
    const author =
      doc.chamber === "Executive"
        ? "Executive Office of the President"
        : doc.agencyName || "Office of the Federal Register";
    return { author, container: federalRegister(doc.frCitation, doc.frDocNumber), url };
  }
  if (doc.docType.endsWith("Floor Speech")) {
    return {
      author: doc.speakerName || doc.politicianName || "U.S. Congress",
      container: congressionalRecord(doc.crCitation),
      url,
    };
  }
  if (doc.docType === "Supreme Court Opinion") {
    return { author: "Supreme Court of the United States", url };
  }
  return null;
}

/** A roll call is the chamber's record: the Senate's own, or the House
 *  Clerk's. */
export function rollCallCitedSource(
  chamber: "senate" | "house",
  sourceUrl: string | null | undefined
): CitedSource | null {
  if (!sourceUrl) return null;
  return {
    author:
      chamber === "senate"
        ? "United States Senate"
        : "Office of the Clerk, U.S. House of Representatives",
    url: sourceUrl,
  };
}

/** A bill or a nomination is Congress's record, as Congress.gov publishes it. */
export function congressGovCitedSource(
  author: "U.S. Congress" | "U.S. Senate",
  url: string | null | undefined
): CitedSource | null {
  return url ? { author, container: "Congress.gov", url } : null;
}
