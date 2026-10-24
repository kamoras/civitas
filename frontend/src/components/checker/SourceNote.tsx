import type { SourceStatus } from "@/types/senator";

/** The backend's line saying a disclosure source's reads are failing, so
 *  the stored filings beside it may not be the newest (an outage is not a
 *  record of nothing new). Nothing while reads succeed. */
export default function SourceNote({
  status,
  className = "",
}: {
  status: SourceStatus | null | undefined;
  className?: string;
}) {
  if (!status?.note) return null;
  return (
    <p
      role="note"
      className={`border-l-2 border-signal-amber pl-3 text-body text-ink ${className}`}
    >
      {status.note}
    </p>
  );
}
