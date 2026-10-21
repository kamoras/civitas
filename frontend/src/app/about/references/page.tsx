import { pageMetadata } from "@/lib/site";
import { AboutPage, Box, P } from "@/components/about/AboutPage";
import BoxGrid from "@/components/shared/BoxGrid";
import { REFERENCES } from "@/components/about/references";

export const metadata = pageMetadata({
  title: "References",
  description:
    "The political science, statistics and machine-learning research behind Civitas's scoring and classification methods.",
  path: "/about/references",
});

type Entry = [string, (typeof REFERENCES)[keyof typeof REFERENCES]];

/** The works in alphabetical groups by the first author's initial, each a
 *  ruled grid under its letter. */
function byInitial(entries: Entry[]): [string, Entry[]][] {
  const groups = new Map<string, Entry[]>();
  for (const e of entries) {
    const letter = e[1].short[0].toUpperCase();
    groups.set(letter, [...(groups.get(letter) ?? []), e]);
  }
  return [...groups];
}

/**
 * Each work is a compact record box: who and when as its title, the full
 * entry as its line, and its anchor (`/about/references#<id>`, what every
 * inline citation links to) on the box itself. The arrival target is
 * outlined, as the old list's was marked.
 */
export default function ReferencesChapter() {
  const entries = Object.entries(REFERENCES).sort(([a], [b]) => a.localeCompare(b)) as Entry[];
  const groups = byInitial(entries);
  return (
    <AboutPage
      href="/about/references"
      eyebrow="Methodology · sources cited"
      title="References"
      lede={<p>The research every method here is built on.</p>}
    >
      <div className="space-y-6 [&_article:target]:outline [&_article:target]:outline-2 [&_article:target]:outline-offset-2 [&_article:target]:outline-signal-cyan">
        {groups.map(([letter, refs], g) => (
          <BoxGrid key={letter} columns={2} label={letter} labelId={`refs-${letter.toLowerCase()}`}>
            {refs.map(([id, ref], i) =>
              g === 0 && i === 0 ? (
                <Box key={id} id={id} title={ref.short} dek={ref.entry} more="what citing means">
                  <P>
                    The research every method in these pages is built on, cited where it is used.
                    Citing a study means a method follows it or was tested the way it describes, not
                    that its authors endorse how it is applied here.
                  </P>
                </Box>
              ) : (
                <Box key={id} id={id} title={ref.short} dek={ref.entry} />
              )
            )}
          </BoxGrid>
        ))}
      </div>
    </AboutPage>
  );
}
