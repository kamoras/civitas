import { pageMetadata } from "@/lib/site";
import { AboutPage, P } from "@/components/about/AboutPage";
import { REFERENCES } from "@/components/about/references";

export const metadata = pageMetadata({
  title: "References",
  description:
    "The political science, statistics and machine-learning research behind Civitas's scoring and classification methods.",
  path: "/about/references",
});

export default function ReferencesChapter() {
  const entries = Object.entries(REFERENCES).sort(([a], [b]) => a.localeCompare(b));
  return (
    <AboutPage
      href="/about/references"
      eyebrow="Methodology · sources cited"
      title="References"
      lede={<p>The research every method in these pages is built on, cited where it is used.</p>}
    >
      <P>
        Citing a study means a method follows it or was tested the way it describes, not that its
        authors endorse how it is applied here.
      </P>
      <ol className="space-y-4">
        {entries.map(([id, ref]) => (
          <li
            key={id}
            id={id}
            className="scroll-mt-[var(--header-clearance)] border-l-3 border-white/[0.09] pl-4 text-sm leading-relaxed text-ink-lo target:border-signal-cyan target:text-ink"
          >
            <span className="block font-mono text-xs uppercase tracking-[0.08em] text-signal-amber">
              {ref.short}
            </span>
            <span className="mt-1 block">{ref.entry}</span>
          </li>
        ))}
      </ol>
    </AboutPage>
  );
}
