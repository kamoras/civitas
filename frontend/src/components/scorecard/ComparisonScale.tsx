/** One figure beside the average it is scored against, on one line: a
 *  block for the figure, a tick for the average. Both numbers are the
 *  scorer's; only the axis (`min`, `max`) is chosen here, wide enough to
 *  hold both. */
export default function ComparisonScale({
  value,
  norm,
  min,
  max,
  valueLabel,
  normLabel,
  axis,
  tone,
}: {
  value: number;
  norm: number;
  min: number;
  max: number;
  /** "This term 37.3%" */
  valueLabel: string;
  /** "all presidents 50.9%" */
  normLabel: string;
  /** Axis end labels, e.g. ["0%", "100%"]. */
  axis: [string, string];
  /** Text and fill classes of the score's colour. */
  tone: { text: string; bg: string };
}) {
  const at = (v: number) => `${((Math.min(Math.max(v, min), max) - min) / (max - min)) * 100}%`;
  return (
    <div role="img" aria-label={`${valueLabel}; ${normLabel}`}>
      <div className="relative h-8" aria-hidden="true">
        <span className="absolute inset-x-0 top-[15px] h-0.5 bg-white/[0.14]" />
        <span className="absolute top-[7px] h-[18px] w-0.5 bg-ink-lo" style={{ left: at(norm) }} />
        <span
          className={`absolute top-[5px] -ml-1.5 h-[22px] w-3 ${tone.bg}`}
          style={{ left: at(value) }}
        />
      </div>
      <div className="relative h-4 font-mono text-xs text-ink-min" aria-hidden="true">
        <span className="absolute left-0">{axis[0]}</span>
        <span className="absolute right-0">{axis[1]}</span>
      </div>
      <p className="mt-1 flex flex-col gap-0.5 font-mono text-xs" aria-hidden="true">
        <span className={tone.text}>
          <span className={`mr-1.5 inline-block h-2.5 w-2 align-middle ${tone.bg}`} />
          {valueLabel}
        </span>
        <span className="text-ink-lo">
          <span className="mr-1.5 inline-block h-2.5 w-0.5 bg-ink-lo align-middle" />
          {normLabel}
        </span>
      </p>
    </div>
  );
}
