import type { SupportMethod } from "@/types/presidentialSupport";

/** The method in the backend's own words, the fitted curves' coefficients,
 * and the raw data. */
export default function MethodNotes({
  method,
  jsonHref,
}: {
  method: SupportMethod;
  jsonHref: string;
}) {
  const fits = Object.entries(method.fits);
  return (
    <div className="space-y-3 font-sans text-sm leading-relaxed text-ink-lo">
      <p>
        <span className="font-mono text-xs text-ink-min">Source · </span>
        <a
          href={method.sourceUrl}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex min-h-6 items-center text-signal-cyan hover:text-phos"
        >
          {method.source}
        </a>
      </p>
      <p>
        <span className="font-mono text-xs text-ink-min">Counted · </span>
        {method.counted}
      </p>
      <p>
        <span className="font-mono text-xs text-ink-min">Position · </span>
        {method.position}
      </p>
      <p>
        <span className="font-mono text-xs text-ink-min">Expected · </span>
        {method.expected}
      </p>
      <p>
        <span className="font-mono text-xs text-ink-min">Range · </span>
        {method.interval}
      </p>
      <p>{method.neutral}</p>
      {fits.length > 0 && (
        // Scrolls on its own at a narrow width or a large text size, not the
        // page: six number columns don't fit a phone at 200% text.
        <div
          className="overflow-x-auto"
          tabIndex={0}
          role="region"
          aria-label="This Congress's fit, by party"
        >
          <table className="w-full border-collapse font-mono text-xs text-ink-lo">
            <caption className="mb-1 text-left text-ink-min">
              This Congress&apos;s fit: logit(rate) = a + b·alignment + b<sub>opp</sub>
              ·min(alignment, 0)
            </caption>
            <thead>
              <tr className="border-b border-white/[0.09] text-left text-ink-min">
                <th className="py-1 font-normal">Party</th>
                <th className="py-1 font-normal">a</th>
                <th className="py-1 font-normal">b</th>
                <th className="py-1 font-normal">b opp</th>
                <th className="py-1 font-normal">Members</th>
                <th className="py-1 font-normal">Votes</th>
              </tr>
            </thead>
            <tbody>
              {fits.map(([party, f]) => (
                <tr key={party} className="border-b border-white/[0.07]">
                  <td className="py-1">{party}</td>
                  <td className="py-1">{f.a}</td>
                  <td className="py-1">{f.b}</td>
                  <td className="py-1">{f.bOpposed}</td>
                  <td className="py-1">{f.members}</td>
                  <td className="py-1">{f.votes}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p className="font-mono text-xs">
        <a
          href={jsonHref}
          download
          className="inline-flex min-h-6 items-center text-signal-cyan hover:text-phos"
        >
          [Download the data, JSON]
        </a>
        {method.computedAt && (
          <span className="ml-3 text-ink-min">Measured {method.computedAt.slice(0, 10)}</span>
        )}
      </p>
    </div>
  );
}
