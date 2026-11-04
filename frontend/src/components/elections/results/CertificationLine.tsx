import { certificationText } from "@/lib/results";
import { safeHref } from "@/lib/formatting";
import { STATE_NAMES } from "@/lib/stateCodes";
import { INLINE_LINK } from "@/lib/controlStyles";
import type { RaceCertification } from "@/types/election";

/** A race's certification under its count: the sentence certificationText
 * words, and the state's certified document linked. Nothing before the
 * state certifies. */
export default function CertificationLine({
  certification,
  state,
}: {
  certification: RaceCertification | null | undefined;
  state: string;
}) {
  if (!certification) return null;
  const href = safeHref(certification.sourceUrl);
  return (
    <p className="mt-2 text-body text-ink">
      {certificationText(certification, STATE_NAMES[state] ?? state)}{" "}
      {href ? (
        <a href={href} target="_blank" rel="noopener noreferrer" className={INLINE_LINK}>
          {certification.sourceName}
        </a>
      ) : (
        <span className="text-ink-lo">{certification.sourceName}</span>
      )}
    </p>
  );
}
