import type { ReactNode } from "react";
import { connection } from "next/server";
import { pageMetadata } from "@/lib/site";
import { fetchRecord } from "@/lib/ssrPayload";
import { formatUtcDate } from "@/lib/formatting";
import Navbar from "@/components/layout/Navbar";
import PageMasthead from "@/components/layout/PageMasthead";
import Footer from "@/components/layout/Footer";
import BoxGrid from "@/components/shared/BoxGrid";
import RecordBox from "@/components/shared/RecordBox";
import { A, Item, List, P } from "@/components/about/AboutPage";
import type { StatFigure } from "@/types/figure";

export const metadata = pageMetadata({
  title: "Environmental Impact",
  description:
    "Civitas runs on one Raspberry Pi 5. What it draws, what we measured and what we assumed, its yearly energy and carbon, and what the figures leave out.",
  path: "/environmental",
});

const BACKEND = process.env.BACKEND_URL || "http://backend:8000";

interface Sourced {
  source: string;
  date: string;
}

/** GET /api/environmental (backend app/environmental.py). The machine, the
 *  models' sizes and the pipelines' hours are read by the site itself; what
 *  it can't read is a dated measurement, a published figure or a labelled
 *  assumption, each with its source and date. The worked-out figures and
 *  every box line are the backend's; a figure not read has no figure and
 *  says so. */
interface Environmental {
  reviewed: string;
  boxes: {
    id: string;
    kicker: string;
    title: string;
    dek: string;
    figure: StatFigure | null;
    source: string | null;
  }[];
  figures: {
    boardPower: Sourced & {
      minWatts: number;
      medianWatts: number;
      peakWatts: number;
      readings: number;
      intervalSeconds: number;
      window: string;
      _whyNotSampled: string;
    };
    assumedWatts: Sourced & { watts: number };
    grid: Sourced & { lbCo2ePerMWh: number; edition: string };
  };
  machine: (Sourced & { model: string | null; cores: number; memoryGiB: number }) | null;
  models: {
    llm?: { name: string; parameters: number; bytes: number; type: string | null; readAt: string };
    embedding?: Record<string, { parameters: number; use: string; readAt: string }>;
  } | null;
}

const day = (iso: string) =>
  formatUtcDate(iso.slice(0, 10), { month: "long", day: "numeric", year: "numeric" });

/** The method and caveats behind each box's "More", from the same fields. */
function moreFor(id: string, e: Environmental): { label: string; children: ReactNode } | null {
  const { boardPower: bp, assumedWatts: aw, grid } = e.figures;
  switch (id) {
    case "machine":
      return {
        label: "what runs on it",
        children: (
          <>
            <P>
              nginx, the Next.js website, the FastAPI backend with its SQLite database and two small
              embedding models, and a llama.cpp server for the language model. All run in Docker on
              this one machine, with an NVMe SSD, a microSD card and a USB flash drive.
            </P>
            <P>
              Cloudflare relays every visit and keeps copies of the site&apos;s scripts and styles
              near visitors; pages and data come from the Pi. The Pi also runs other, unrelated
              projects. We count all of its power against Civitas rather than try to split it.
            </P>
            <P>
              The model, cores and memory are read from this server&apos;s own{" "}
              <code>/proc/cpuinfo</code> and <code>/proc/meminfo</code> each time the page is built.
            </P>
          </>
        ),
      };
    case "models": {
      const llm = e.models?.llm;
      const embedding = Object.entries(e.models?.embedding ?? {});
      if (!llm) return null;
      return {
        label: "each model",
        children: (
          <List>
            <Item label={llm.name}>
              {llm.parameters.toLocaleString("en-US")} parameters
              {llm.type ? `, ${llm.type}` : ""}. Finds claims in news articles and writes a few
              short, labelled summaries. Read from the llama.cpp server&apos;s own model metadata (
              <code>/v1/models</code>), {day(llm.readAt)}.
            </Item>
            {embedding.map(([name, m]) => (
              <Item key={name} label={name}>
                {m.parameters.toLocaleString("en-US")} parameters, for {m.use}. Counted from the
                model file&apos;s tensor shapes, {day(m.readAt)}.
              </Item>
            ))}
          </List>
        ),
      };
    }
    case "pipelines":
      return {
        label: "how this is counted",
        children: (
          <P>
            From the run records of all five nightly pipelines (Senate, supplementary, House, stock
            trades, election): the hours on each of the last seven whole days that any of them was
            running, two at once counted once.
          </P>
        ),
      };
    case "board":
      return {
        label: "how the board was measured",
        children: (
          <>
            <P>
              The Pi 5&apos;s power-management chip reports the current and voltage on each of its
              supply rails (<code>vcgencmd pmic_read_adc</code>). Each rail&apos;s current times its
              voltage, added up, is the power the board itself uses. We read it every{" "}
              {bp.intervalSeconds} seconds on the production machine, {bp.window}, on {day(bp.date)}
              : {bp.readings} readings, lowest {bp.minWatts} W, median {bp.medianWatts} W, highest{" "}
              {bp.peakWatts} W.
            </P>
            <P>
              It covers the processor, memory and the board&apos;s own circuits. Anything powered
              from the USB ports or the PCIe slot, and the loss in converting the supply&apos;s 5
              volts, is outside it.
            </P>
            <P>Why this isn&apos;t measured all the time: {bp._whyNotSampled}</P>
          </>
        ),
      };
    case "assumed":
      return {
        label: "what the board can't see",
        children: (
          <P>
            The SSD, microSD card, USB drive, fan and the power supply&apos;s own losses. The
            board&apos;s power chip can&apos;t see them and we have no meter at the wall. That gap
            is why we budget {aw.watts} W rather than quote the board figure.
          </P>
        ),
      };
    case "carbon":
      return {
        label: "which grid",
        children: (
          <P>
            The U.S. average from EPA {grid.edition}, {grid.lbCo2ePerMWh} lb CO₂e per MWh, the
            latest edition. We don&apos;t know the mix of the local grid and hold no
            renewable-energy certificates, so a cleaner or dirtier local grid would move this figure
            either way.
          </P>
        ),
      };
    case "request":
      return {
        label: "why this isn't like-for-like",
        children: (
          <>
            <P>
              Our model is far smaller and less capable than the ones behind commercial chat
              services, and it does narrow jobs: finding a claim in an article, not holding a
              conversation. We didn&apos;t catch a language-model request while measuring, so this
              is an upper bound at the board&apos;s highest reading. Even allowing for the parts the
              board misses, it is a small fraction of a chat query, for a much smaller job.
            </P>
            <P>
              Running the model here also means no request, and nothing about a visitor, is sent to
              an AI company. What is recorded about visits is on the{" "}
              <A href="/about/data#privacy">data and privacy page</A>.
            </P>
          </>
        ),
      };
    default:
      return null;
  }
}

export default async function EnvironmentalPage() {
  // Rendered per request: `next build` can't reach the backend.
  await connection();
  const env = await fetchRecord<Environmental>(
    `${BACKEND}/api/environmental`,
    { next: { revalidate: 3600 } },
    "boxes",
    "figures"
  );
  if (!env) throw new Error("/api/environmental returned no figures");

  return (
    <>
      <Navbar />
      <main id="main-content" tabIndex={-1} className="pt-[var(--header-clearance)] pb-16 px-4">
        <div className="max-w-3xl mx-auto font-sans">
          <PageMasthead
            className="mb-6"
            eyebrow="Environmental · what this service costs to run"
            title="Environmental impact"
          >
            <p>
              Civitas uses AI models, so you should know what running it costs: what we measured,
              what we assumed, and what we left out.
            </p>
          </PageMasthead>

          <BoxGrid columns={2} label="What it costs to run" labelId="costs">
            {env.boxes.map((b) => (
              <RecordBox
                key={b.id}
                id={b.id}
                kicker={b.kicker}
                title={b.title}
                dek={b.dek}
                figure={b.figure}
                more={b.figure ? moreFor(b.id, env) : null}
              >
                {b.source && <p className="text-sm text-ink-min">Source: {b.source}.</p>}
              </RecordBox>
            ))}
            <RecordBox
              id="limits"
              kicker="Not counted"
              title="What these figures leave out"
              dek="The rest of the network, your device, making the hardware, and the local grid's mix."
              more={{
                label: "each, and why",
                children: (
                  <List>
                    <Item label="The rest of the network">
                      The home router and modem, the internet between the Pi and a visitor, and
                      Cloudflare&apos;s servers all use energy we can&apos;t measure or attribute.
                    </Item>
                    <Item label="Your device">
                      The phone or computer you&apos;re reading this on is not counted.
                    </Item>
                    <Item label="Making the hardware">
                      Manufacturing the Pi and its drives has a carbon cost. Raspberry Pi Ltd does
                      not publish a lifecycle assessment, so we don&apos;t put a number on it.
                    </Item>
                    <Item label="The local grid">
                      We hold no renewable-energy certificates. A cleaner or dirtier local grid
                      would move the carbon figure in either direction.
                    </Item>
                  </List>
                ),
              }}
            />
          </BoxGrid>
          <p className="mt-6 font-mono text-xs text-ink-min">Last reviewed: {day(env.reviewed)}.</p>
        </div>
      </main>
      <Footer />
    </>
  );
}
