import Link from "next/link";
import { onThisPage } from "@/lib/onThisPage";
import ShareSectionButton from "@/components/share/ShareSectionButton";
import { Dot } from "@/components/scorecard/WhereTheyStand";
import StripPoints, { BigFigure, StripAxis, type StripPoint } from "@/components/shared/Strip";
import { overlay } from "@/lib/chartColors";
import { termsPhrase } from "@/lib/court";
import { SHARE_SECTION_ATTR, shareMetaAttrs } from "@/lib/shareImage";
import type { JusticeFigures as Figures, JusticeMeasure } from "@/types/justice";

/** A dot's height in the band, the same every render, so ties stay
 *  countable without moving. */
const JITTER = [10, 4, 16, 7, 13, 1, 19, 10, 6];

/** The strip's axis, on the measure's own scale: shares 0 to 100%, counts
 *  and years from 0 up to a round five, a Martin-Quinn position the same
 *  distance either side of 0 so the middle of the scale shows. */
export function stripDomain(m: Pick<JusticeMeasure, "unit" | "peers">): [number, number] {
  const values = m.peers.map((p) => p.value);
  if (m.unit === "share") return [0, 1];
  if (m.unit === "position") {
    const far = Math.max(1, Math.ceil(Math.max(0, ...values.map(Math.abs))));
    return [-far, far];
  }
  return [0, Math.max(5, Math.ceil(Math.max(0, ...values) / 5) * 5)];
}

function axisLabel(m: JusticeMeasure, v: number): string {
  if (m.unit === "share") return `${Math.round(v * 100)}%`;
  if (m.unit === "position") return v > 0 ? `+${v}` : v < 0 ? `−${-v}` : "0";
  return String(v);
}

function Strip({ m, justiceId }: { m: JusticeMeasure; justiceId: string }) {
  const [lo, hi] = stripDomain(m);
  const at = (v: number) => ((Math.max(lo, Math.min(hi, v)) - lo) / (hi - lo)) * 100;
  const others = m.peers.filter((p) => p.id !== justiceId);
  const top = (i: number) => 12 + JITTER[i % JITTER.length] / 2;
  const points: StripPoint[] = m.peers.map((p) => ({
    id: p.id,
    name: p.name,
    shown: p.shown,
    rank: p.rank,
    href: `/politicians/${encodeURIComponent(p.id)}`,
    x: at(p.value),
    y: p.id === justiceId ? 22 : top(others.indexOf(p)) + 4,
  }));
  return (
    <div className="min-w-0 px-2">
      {/* The table below is the full text form of this strip. */}
      <StripPoints
        label={`${m.label}, sitting justices`}
        points={points}
        startId={justiceId}
        className="h-10"
      >
        <div className="absolute inset-x-0 top-[22px] h-px" style={{ background: overlay(0.4) }} />
        {m.unit === "position" && (
          <span
            className="absolute left-1/2 top-2 h-7 border-l"
            style={{ borderColor: overlay(0.4) }}
          />
        )}
        {m.median != null && (
          <span
            className="absolute top-1 h-8 -translate-x-1/2 border-l-2 border-dashed border-ink-hi"
            style={{ left: `${at(m.median)}%` }}
          />
        )}
        {others.map((p, i) => (
          <Dot
            key={p.id}
            size={8}
            fill="var(--chart-muted)"
            className="absolute -translate-x-1/2"
            style={{ left: `${at(p.value)}%`, top: `${top(i)}px` }}
          />
        ))}
        {m.value != null && (
          <Dot
            size={14}
            fill="var(--ink-hi)"
            ring="var(--phos)"
            className="absolute top-[15px] -translate-x-1/2"
            style={{ left: `${at(m.value)}%` }}
          />
        )}
      </StripPoints>
      <StripAxis
        start={axisLabel(m, lo)}
        end={axisLabel(m, hi)}
        tick={
          m.median != null && m.medianShown
            ? { at: at(m.median), text: `median ${m.medianShown}` }
            : null
        }
      />
    </div>
  );
}

function Row({ m, justiceId }: { m: JusticeMeasure; justiceId: string }) {
  return (
    <figure
      aria-labelledby={`figure-${m.key}`}
      className="grid grid-cols-1 gap-x-6 gap-y-2 border-t border-white/25 px-4 py-4 [h3+&]:border-t-0 sm:px-6 md:grid-cols-[10rem_minmax(0,1fr)]"
    >
      <div>
        <div className="text-[13px] font-semibold text-ink-lo">{m.label}</div>
        <BigFigure
          shown={m.shown}
          className="text-[28px] font-semibold leading-tight text-ink-hi"
        />
      </div>
      <div className="min-w-0">
        <p className="mb-1 text-sm text-ink">{m.why}</p>
        <Strip m={m} justiceId={justiceId} />
      </div>
      <figcaption id={`figure-${m.key}`} className="text-sm text-ink md:col-span-2">
        <span className="sr-only">{m.label}: </span>
        {m.sentence}{" "}
        <a href={m.source.url} className="text-phos underline underline-offset-2">
          {m.source.name}
        </a>
      </figcaption>
      <div className="sr-only">
        <table>
          <caption>{m.label}: each sitting justice, lowest first</caption>
          <thead>
            <tr>
              <th scope="col">Justice</th>
              <th scope="col">{m.label}</th>
            </tr>
          </thead>
          <tbody>
            {m.peers.map((p) => (
              <tr key={p.id}>
                <th scope="row">
                  <Link href={`/politicians/${encodeURIComponent(p.id)}`}>{p.name}</Link>
                </th>
                <td>{p.shown}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </figure>
  );
}

/**
 * The justice's record, figure by figure, each beside the same figure for
 * every sitting justice: one dot per justice, this justice ringed, the
 * median dashed. Every figure, median and sentence is the API's
 * (services/justice_figures.py); this only places them.
 */
export default function JusticeFigures({
  figures,
  justiceId,
  name,
}: {
  figures: Figures;
  justiceId: string;
  name: string;
}) {
  const measures = figures.measures;
  return (
    <section
      id="figures"
      aria-labelledby="figures-heading"
      {...{ [SHARE_SECTION_ATTR]: "figures" }}
      {...shareMetaAttrs({
        source: "Supreme Court Database, Oyez, Federal Judicial Center and Martin-Quinn scores",
        asOf: null,
      })}
      className="font-sans"
    >
      <div className="border-t-[3px] border-ink-hi pt-3">
        <h2
          id="figures-heading"
          {...onThisPage("Beside the other justices")}
          className="font-display text-[22px] font-semibold text-ink-hi sm:text-[26px]"
        >
          {name} beside the other justices
        </h2>
        <p className="mt-1 text-sm text-ink-lo">
          Each dot is one of the {figures.peers} sitting justices
          {figures.terms.length ? `, over ${termsPhrase(figures.terms)}` : ""}.
        </p>
      </div>
      <div className="mt-3 border border-white/25 bg-surface">
        {measures.map((m, i) => (
          <div key={m.key}>
            {m.question !== measures[i - 1]?.question && (
              <h3
                className={`px-4 pt-4 font-display text-lg font-semibold text-ink-hi sm:px-6 ${i ? "border-t-2 border-ink-hi/60" : ""}`}
              >
                {m.question}
              </h3>
            )}
            <Row m={m} justiceId={justiceId} />
          </div>
        ))}
      </div>
      <div className="mt-2 flex flex-wrap items-center justify-between gap-3">
        <p className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-ink-lo" aria-hidden="true">
          <span>
            <Dot size={8} fill="var(--chart-muted)" className="mr-1 inline-block" />
            another justice
          </span>
          <span>
            <Dot
              size={12}
              fill="var(--ink-hi)"
              ring="var(--phos)"
              className="mr-1 inline-block align-middle"
            />
            {name}
          </span>
          <span>┆ median</span>
        </p>
        <ShareSectionButton label="Beside the other justices" />
      </div>
    </section>
  );
}
