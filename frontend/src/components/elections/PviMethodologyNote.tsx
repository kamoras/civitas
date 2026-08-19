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
    // Press Start 2P at 9px was unreadable, and this is the caveat that stops
    // a lean figure being read as a forecast — the last thing that should be
    // the smallest text on the page. Mono at 12px, above the contrast floor.
    <p className="font-sans text-xs leading-relaxed text-ink-lo">
      {note}
      {source ? ` SOURCE: ${source}.` : ""}
      {window ? ` WINDOW: ${window}.` : ""}
    </p>
  );
}
