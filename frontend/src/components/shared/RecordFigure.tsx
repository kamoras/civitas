import type { CountdownFigure, DotsFigure, Figure, SplitFigure, StatFigure } from "@/types/figure";
import { overlay, partyMark } from "@/lib/chartColors";

/**
 * A record box's figure: a counted number, a yea-nay split, a party's dots
 * or a comment period's countdown. The homepage's front page and every
 * RecordBox draw these, so a figure looks the same on every page. Each is
 * an image with its own text (role="img" and a label that says what it
 * shows); every number and caption comes from the backend.
 *
 * Sizes: "lead" and "tier" are the front page's (set by the window's
 * width); "box" and "boxLead" follow the box they sit in (container
 * queries), so a figure in a narrow column is drawn smaller rather than
 * pushed under its text.
 */
export type FigureSize = "lead" | "tier" | "box" | "boxLead";

const VALUE: Record<FigureSize, string> = {
  lead: "text-[3.5rem] sm:text-[4.5rem]",
  tier: "text-[2.125rem]",
  box: "text-[1.625rem] @min-[26rem]:text-[2.125rem]",
  boxLead: "text-[2.5rem] @min-[40rem]:text-[3.5rem]",
};

const YEA = "var(--phos)";
const NAY = "var(--signal-red)";

function splitLabel(f: SplitFigure): string {
  return `${f.yeas} yea to ${f.nays} nay. ${f.caption}.`;
}

function BigSplit({ f, size }: { f: SplitFigure; size: FigureSize }) {
  return (
    <div role="img" aria-label={splitLabel(f)}>
      <div
        className={`whitespace-nowrap text-center font-display font-semibold leading-none text-ink-hi ${VALUE[size]}`}
      >
        {f.yeas}–{f.nays}
      </div>
      <div className="mt-1.5 text-center text-sm text-ink-lo">yeas to nays</div>
      <SplitBar f={f} height={size === "lead" || size === "boxLead" ? "h-6" : "h-3"} />
      <div className="mt-1 flex justify-between gap-2 text-sm text-ink-lo">
        <span>{f.yeas} yea</span>
        <span>{f.nays} nay</span>
      </div>
      <div className="mt-0.5 text-center text-sm text-ink-hi">{f.caption}</div>
    </div>
  );
}

function SplitBar({ f, height }: { f: SplitFigure; height: string }) {
  return (
    <div className={`mt-3 flex gap-0.5 ${height}`}>
      <span style={{ flex: f.yeas, background: YEA }} />
      <span style={{ flex: f.nays, background: NAY }} />
    </div>
  );
}

function MiniSplit({ f }: { f: SplitFigure }) {
  return (
    <div role="img" aria-label={splitLabel(f)} className="mb-0.5 mt-1.5 w-full max-w-[180px]">
      <div className="flex h-2 gap-px">
        <span style={{ flex: f.yeas, background: YEA }} />
        <span style={{ flex: f.nays, background: NAY }} />
      </div>
    </div>
  );
}

// Dots per row: a Senate party in three rows, a House party (200-odd
// members) in no more than eight.
function dotColumns(n: number): number {
  return n <= 60 ? 15 : n <= 150 ? 20 : 30;
}

function Dots({ f, mini = false }: { f: DotsFigure; mini?: boolean }) {
  const cols = dotColumns(f.voting);
  const rows = Math.ceil(f.voting / cols);
  const pitch = 12;
  const mark = partyMark(f.party);
  return (
    <div role="img" aria-label={`${f.caption}.`} className={mini ? "mt-1.5 max-w-[180px]" : ""}>
      <svg
        viewBox={`0 0 ${cols * pitch} ${rows * pitch}`}
        className="block w-full max-w-[180px]"
        aria-hidden="true"
      >
        {Array.from({ length: f.voting }, (_, i) => {
          const breaker = i === f.voting - 1;
          return (
            <circle
              key={i}
              cx={(i % cols) * pitch + pitch / 2}
              cy={Math.floor(i / cols) * pitch + pitch / 2}
              r={breaker ? 3.6 : 4.4}
              // The breaker is a ring, not another colour: told apart by
              // shape, never by a party's colour.
              fill={breaker ? "none" : mark}
              stroke={breaker ? "var(--ink-hi)" : undefined}
              strokeWidth={breaker ? 2 : undefined}
            />
          );
        })}
      </svg>
      {!mini && <div className="mt-2 text-sm text-ink-lo">{f.caption}</div>}
    </div>
  );
}

export function daysLeftWords(f: CountdownFigure): string {
  const deadline = f.caption !== undefined;
  if (f.daysLeft === 0) return deadline ? "Closes today" : "Comments close today";
  const what = deadline ? (f.caption ? ` ${f.caption}` : "") : " to comment";
  return `${f.daysLeft} day${f.daysLeft === 1 ? "" : "s"} left${what}`;
}

/** How much of a comment period has gone; nothing for a deadline with no
 *  period behind it. */
function Elapsed({ f, className = "" }: { f: CountdownFigure; className?: string }) {
  if (f.totalDays == null || f.elapsedPercent == null) return null;
  return (
    <div
      role="img"
      aria-label={`${f.totalDays - f.daysLeft} of the period's ${f.totalDays} days gone. ${daysLeftWords(f)}.`}
      className={`flex h-2 w-full max-w-[180px] ${className}`}
      style={{ background: overlay(0.15) }}
    >
      <span style={{ width: `${f.elapsedPercent}%`, background: "var(--signal-amber)" }} />
    </div>
  );
}

function Countdown({ f, size }: { f: CountdownFigure; size: FigureSize }) {
  // Without a period's bar (which carries its own text), the number is
  // the image and says what closes.
  const bare = f.totalDays == null || f.elapsedPercent == null;
  return (
    <div
      className="text-center"
      {...(bare ? { role: "img", "aria-label": `${daysLeftWords(f)}.` } : {})}
    >
      <div
        className={`whitespace-nowrap font-display font-semibold leading-none text-signal-amber ${
          size === "lead" ? VALUE.tier : VALUE[size]
        }`}
      >
        {f.daysLeft === 0 ? "Today" : f.daysLeft}
      </div>
      <div className="mt-1 text-sm text-ink-lo">
        {f.daysLeft === 0
          ? f.caption !== undefined
            ? "closes"
            : "comments close"
          : `day${f.daysLeft === 1 ? "" : "s"} left${f.caption ? ` ${f.caption}` : ""}`}
      </div>
      <Elapsed f={f} className="mx-auto mt-2" />
    </div>
  );
}

function Stat({ f, size }: { f: StatFigure; size: FigureSize }) {
  const value = f.text ?? f.value.toLocaleString("en-US");
  return (
    <div role="img" aria-label={`${value} ${f.caption}.`} className="text-center">
      <div
        className={`whitespace-nowrap font-display font-semibold leading-none text-ink-hi ${VALUE[size]}`}
      >
        {value}
      </div>
      <div className="mt-1.5 text-sm text-ink-lo">{f.caption}</div>
    </div>
  );
}

/** A figure at full size: beside a lead or tier story, or in a box. */
export default function RecordFigure({ f, size }: { f: Figure; size: FigureSize }) {
  switch (f.type) {
    case "split":
      return <BigSplit f={f} size={size} />;
    case "dots":
      return <Dots f={f} />;
    case "countdown":
      return <Countdown f={f} size={size} />;
    case "stat":
      return <Stat f={f} size={size} />;
  }
}

/** A list item's small figure, where its data has one. */
export function InlineRecordFigure({ f }: { f: Figure | null }) {
  if (!f) return null;
  if (f.type === "split") return <MiniSplit f={f} />;
  if (f.type === "dots") return <Dots f={f} mini />;
  if (f.type === "countdown") return <Elapsed f={f} className="mb-0.5 mt-1.5" />;
  return null;
}
