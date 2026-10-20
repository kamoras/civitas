import { P, List, Item, A } from "@/components/about/AboutPage";

/** The license of Civitas's data and the sources whose terms carry over,
 *  stated once and shown on /about/data#license and /developers#license.
 *  The API names the same page (public.LICENSE_URL, the CSVs' Link header,
 *  the spec's info.license): change the wording here and both pages follow. */
export default function DataLicense() {
  return (
    <>
      <P>
        What Civitas produces from the public record (the scores and their breakdowns, the
        classifications, the computed district lean, the API&apos;s answers and the CSV files) is
        shared under{" "}
        <a
          href="https://creativecommons.org/licenses/by/4.0/"
          className="text-signal-cyan underline underline-offset-2 hover:text-phos"
        >
          Creative Commons Attribution 4.0 (CC BY 4.0)
        </a>
        : use it for any purpose, commercial included, and credit Civitas with a link. The code is
        separate, under the{" "}
        <a
          href="https://github.com/kamoras/civitas/blob/main/LICENSE"
          className="text-signal-cyan underline underline-offset-2 hover:text-phos"
        >
          GNU AGPL 3.0
        </a>
        .
      </P>
      <P>Some outputs are built on sources with terms of their own, and those terms carry over:</P>
      <List>
        <Item label="The Supreme Court record: non-commercial only">
          Justices&apos; votes case by case, their shares and agreement, and the appointer estimate
          are built from the Supreme Court Database (CC BY-NC 3.0 United States) and Oyez (CC BY-NC
          4.0). Reuse them for non-commercial purposes only, crediting both. The Martin-Quinn
          positions carry no stated license; cite Martin and Quinn (2002).
        </Item>
        <Item label="Wikipedia-derived figures: share-alike">
          The election map&apos;s current polls and the ranges built on them, and the district Cook
          PVI figures read from pinned Wikipedia revisions, carry Wikipedia&apos;s CC BY-SA 4.0:
          share what you build on them under the same license, crediting Wikipedia contributors
          (Cook&apos;s figures remain the Cook Political Report&apos;s).
        </Item>
        <Item label="Inputs that ask for credit">
          The computed district lean uses the Voting and Election Science Team&apos;s precinct
          results (CC BY 4.0, credit them); the MIT Election Data and Science Lab&apos;s returns and
          the Cooperative Election Study are CC0. The election map&apos;s past polls are Silver
          Bulletin&apos;s, used under its terms and not republished here.
        </Item>
        <Item label="Figures used with citation">
          Voteview&apos;s positions, the C-SPAN Historians Survey and the American Presidency
          Project&apos;s approval series state no open license. Civitas&apos;s scores computed from
          them are Civitas&apos;s; to reuse the source figures themselves, go to the source and
          follow its terms.
        </Item>
        <Item label="Government records">
          Votes, bills, campaign filings, disclosures and Federal Register documents are works of
          the U.S. government and in the public domain (17 U.S.C. § 105); quoting them needs no
          license.
        </Item>
        <Item label="Not covered">
          News text quoted in the Action Center belongs to its outlets, and photos to their sources;
          both are shown with credit and are not licensed by Civitas.
        </Item>
      </List>
      <P>
        To cite a score, name the member, the score, the scoring version and the date it was
        computed (each profile&apos;s &ldquo;methods, data &amp; citation&rdquo; gives all four, as
        does the API&apos;s <code className="font-mono text-xs text-ink-hi">algorithmVersion</code>{" "}
        and <code className="font-mono text-xs text-ink-hi">scoredOn</code>), and link the
        record&apos;s page. How each score is computed:{" "}
        <A href="/about/scores">how senators and representatives are compared</A>.
      </P>
    </>
  );
}
