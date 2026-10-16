import type { AgencySource } from "@/types/agency";
import { readOn } from "@/lib/agencies";

/** A block's source and the day Civitas read it, inside the block so a
 * shared image of it carries both. */
export default function SourceLine({
  source,
  asOf,
  detail,
}: {
  source: AgencySource;
  asOf: string | null;
  /** What exactly was read: "toptier agencies, FY2026 through Q4". */
  detail?: string;
}) {
  return (
    <p className="mt-3 font-mono text-xs text-ink-min">
      Source:{" "}
      <a
        href={source.url}
        target="_blank"
        rel="noopener noreferrer"
        className="underline decoration-white/30 underline-offset-2 hover:text-phos"
      >
        {source.name}
      </a>
      {detail ? `, ${detail}` : ""} · read {readOn(asOf)}
    </p>
  );
}
