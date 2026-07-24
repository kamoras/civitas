import type { PviMeta } from "@/types/election";

// Shown under the /elections map AND next to each race page's PVI figure so
// the methodology caveat is never written twice. The /pvi response's `meta`
// block may be missing (older/cached backend responses, or callers like the
// race page that never fetch /pvi) — fall back to static text.
const FALLBACK_NOTE =
  "Cook-PVI-style partisan lean relative to the national presidential vote. Measures lean, not a race forecast.";

export default function PviMethodologyNote({ meta }: { meta?: PviMeta | null }) {
  const note = meta?.note || FALLBACK_NOTE;
  const source = meta?.states?.source;
  const window = meta?.states?.window;
  return (
    <p className="font-pixel text-[9px] text-matrix-green/40 mt-3 leading-relaxed">
      {note}
      {source ? ` SOURCE: ${source}.` : ""}
      {window ? ` WINDOW: ${window}.` : ""}
    </p>
  );
}
