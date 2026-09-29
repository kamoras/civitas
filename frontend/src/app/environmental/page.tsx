import { pageMetadata } from "@/lib/site";
import Navbar from "@/components/layout/Navbar";
import PageMasthead from "@/components/layout/PageMasthead";
import Footer from "@/components/layout/Footer";
import {
  Summary,
  Point,
  Section,
  P,
  List,
  Item,
  More,
  Facts,
  Fact,
  A,
} from "@/components/about/AboutPage";

export const metadata = pageMetadata({
  title: "Environmental Impact",
  description:
    "Civitas runs on one Raspberry Pi 5. What it draws, what we measured and what we assumed, its yearly energy and carbon, and what the figures leave out.",
  path: "/environmental",
});

/* Every figure below is measured on the production machine, taken from a
   named public source, or labelled as our assumption; each has a date.
   The previous version quoted someone else's meter reading as our own, used a
   grid factor from an eGRID edition that doesn't exist, said the pipeline ran
   the language model for ~3 hours a night (it runs none), and said there was
   no CDN (the site is behind Cloudflare). Re-measure before changing a number:
   the method for each is in its `More` block. */

// Assumed whole-device draw and the grid factor it is multiplied by.
const ASSUMED_WATTS = 7;
const HOURS_PER_YEAR = 24 * 365;
const KWH_PER_YEAR = (ASSUMED_WATTS * HOURS_PER_YEAR) / 1000; // 61.3
// EPA eGRID2023, U.S. total output emission rate: 770.9 lb CO2e/MWh.
const GRID_KG_PER_KWH = 0.3497;
const KG_CO2E_PER_YEAR = KWH_PER_YEAR * GRID_KG_PER_KWH; // 21.4
// EPA Greenhouse Gas Equivalencies Calculator (updated 2026-08-04).
const KG_CO2E_PER_MILE = 0.393;
const KWH_PER_PHONE_CHARGE = 0.019;

const fmt = (n: number, digits = 0) =>
  n.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });

export default function EnvironmentalPage() {
  return (
    <>
      <Navbar />
      <main id="main-content" tabIndex={-1} className="pt-[var(--header-clearance)] pb-16 px-4">
        {/* `font-sans`: this is reading, not data. The body element is
            `font-mono`, and prose that names no face inherits it. */}
        <div className="max-w-3xl mx-auto font-sans">
          <PageMasthead
            className="mb-6"
            eyebrow="Environmental · what this service costs to run"
            title="Environmental impact"
          >
            <p>
              Civitas uses AI models, so you have a right to know what running it costs. This is
              what we measured, what we assumed, and what we left out.
            </p>
          </PageMasthead>

          <div className="mt-10 space-y-12">
            <Summary>
              <Point>
                The whole site, including its AI models, runs on one Raspberry Pi 5 at home: no
                cloud hosting and no AI company&apos;s data centre. Cloudflare relays visits to it.
              </Point>
              <Point>
                We budget {ASSUMED_WATTS} watts for it, about {fmt(KWH_PER_YEAR)} kWh a year. That
                is an assumption set above what we measured, not a meter reading at the wall.
              </Point>
              <Point>
                At the U.S. grid average that is about {fmt(KG_CO2E_PER_YEAR)} kg of CO₂e a year,
                roughly what an average car emits in {fmt(KG_CO2E_PER_YEAR / KG_CO2E_PER_MILE)}{" "}
                miles.
              </Point>
              <Point>
                Most of the machine&apos;s work is the nightly data pipelines. The language model
                runs for about half an hour a day.
              </Point>
            </Summary>

            <Section id="machine" title="The machine">
              <Facts>
                <Fact label="Computer">
                  Raspberry Pi 5, 16 GB of memory, four ARM Cortex-A76 cores
                </Fact>
                <Fact label="Storage">An NVMe SSD, a microSD card and a USB flash drive</Fact>
                <Fact label="Services">
                  nginx, the Next.js website, the FastAPI backend with its SQLite database and two
                  small embedding models, and a llama.cpp server running LFM2.5-1.2B-Instruct
                  (quantised to 4 bits). All run in Docker on this one machine.
                </Fact>
                <Fact label="Where">At the developer&apos;s home. No hosting provider.</Fact>
                <Fact label="In front of it">
                  Cloudflare. Its network relays every visit and keeps copies of the site&apos;s
                  scripts and styles near visitors; pages and data come from the Pi.
                </Fact>
              </Facts>
              <P>
                The Pi also runs other, unrelated projects. We count all of its power against
                Civitas rather than try to split it.
              </P>
            </Section>

            <Section id="work" title="What it spends its time on">
              <List>
                <Item label="Nightly pipelines">
                  Fetching and scoring the public record: votes, bills, campaign finance,
                  disclosures, elections and documents. Over the eight days to September 28, 2026
                  they ran between 4 and 20 hours a day, about 10½ on average. This work is
                  embedding models and database queries; it makes no language-model calls.
                </Item>
                <Item label="The language model">
                  Used by the hourly Action Center refresh to find claims in news articles, and for
                  a few short, labelled summaries. On September 28–29, 2026 it answered 160 requests
                  in 24 hours, averaging 11 seconds each: about 30 minutes of work a day.
                </Item>
                <Item label="Serving the site">
                  Answering page and data requests. Most of the day the machine is otherwise idle.
                </Item>
              </List>
            </Section>

            <Section id="energy" title="Energy">
              <Facts>
                <Fact label="Measured on the board">
                  {" "}
                  3.3 watts on average during the nightly pipeline, its busiest stretch, rising to
                  6.9 W at peak (86 readings, 15 seconds apart, September 29, 2026). This covers the
                  processor, memory and the board&apos;s own circuits.
                </Fact>
                <Fact label="Not measured">
                  The SSD, microSD card, USB drive, fan and the power supply&apos;s own losses. The
                  board&apos;s power chip can&apos;t see them and we have no meter at the wall.
                </Fact>
                <Fact label="What we assume">
                  {ASSUMED_WATTS} watts for the whole machine, around the clock:{" "}
                  {fmt(KWH_PER_YEAR, 1)} kWh a year ({ASSUMED_WATTS} W × 24 h × 365 days). That is
                  enough to charge a smartphone about{" "}
                  {fmt(Math.round(KWH_PER_YEAR / KWH_PER_PHONE_CHARGE / 100) * 100)} times.
                </Fact>
              </Facts>
              <More label="How the board was measured">
                <P>
                  The Pi 5&apos;s power-management chip reports the current and voltage on each of
                  its supply rails (<code>vcgencmd pmic_read_adc</code>). Multiplying each
                  rail&apos;s current by its voltage and adding them up gives the power the board
                  itself uses. We read it every 15 seconds on the production machine while the
                  Senate pipeline ran, from 05:01 to 05:22 UTC on September 29, 2026: lowest 1.9 W,
                  median 3.1 W, highest 6.9 W.
                </P>
                <P>
                  It is a partial figure by construction: anything powered from the USB ports or the
                  PCIe slot, and the loss in converting the supply&apos;s 5 volts, is outside it.
                  That gap is why we budget {ASSUMED_WATTS} W rather than quote the board figure.
                </P>
              </More>
            </Section>

            <Section id="carbon" title="Carbon">
              <Facts>
                <Fact label="Grid factor">
                  {GRID_KG_PER_KWH.toFixed(3)} kg CO₂e per kWh, the U.S. average (EPA eGRID2023, the
                  latest edition). We don&apos;t know the mix of the local grid, so we use the
                  national figure.
                </Fact>
                <Fact label="Per year">
                  About {fmt(KG_CO2E_PER_YEAR, 1)} kg CO₂e ({fmt(KWH_PER_YEAR, 1)} kWh ×{" "}
                  {GRID_KG_PER_KWH.toFixed(3)})
                </Fact>
                <Fact label="For scale">
                  About {fmt(KG_CO2E_PER_YEAR / KG_CO2E_PER_MILE)} miles in an average gasoline car
                  (EPA: {KG_CO2E_PER_MILE} kg CO₂e per mile)
                </Fact>
              </Facts>
            </Section>

            <Section id="cloud" title="Compared with a cloud AI service">
              <P>
                A like-for-like comparison isn&apos;t possible. Our model is far smaller and less
                capable than the ones behind commercial chat services, and it does narrow jobs:
                finding a claim in an article, not holding a conversation.
              </P>
              <P>
                For scale, Epoch AI estimated in 2025 that a typical ChatGPT query to GPT-4o uses
                about 0.3 Wh. We didn&apos;t catch a language-model request while measuring, so here
                is an upper bound instead: at the board&apos;s highest reading (6.9 W), an 11-second
                request uses about 0.02 Wh on the board. Even allowing for the parts the board
                figure misses, that is a small fraction of 0.3 Wh, for a much smaller job.
              </P>
              <P>
                Running the model here also means no request, and nothing about a visitor, is sent
                to an AI company. What is recorded about visits is on the{" "}
                <A href="/about/data#privacy">data and privacy page</A>.
              </P>
            </Section>

            <Section id="limits" title="What these figures leave out">
              <List>
                <Item label="The rest of the network">
                  The home router and modem, the internet between the Pi and a visitor, and
                  Cloudflare&apos;s servers all use energy we can&apos;t measure or attribute.
                </Item>
                <Item label="Your device">
                  The phone or computer you&apos;re reading this on is not counted.
                </Item>
                <Item label="Making the hardware">
                  Manufacturing the Pi and its drives has a carbon cost. Raspberry Pi Ltd does not
                  publish a lifecycle assessment, so we don&apos;t put a number on it.
                </Item>
                <Item label="The local grid">
                  We hold no renewable-energy certificates. A cleaner or dirtier local grid would
                  move the carbon figure in either direction.
                </Item>
              </List>
            </Section>

            <Section id="sources" title="Sources">
              <List>
                <Item label="Board power">
                  The Pi&apos;s own power-management chip (<code>vcgencmd pmic_read_adc</code>) on
                  the production machine, September 29, 2026.
                </Item>
                <Item label="Workload">
                  The production database&apos;s pipeline run records and the llama.cpp
                  server&apos;s request log, September 21–29, 2026.
                </Item>
                <Item label="Grid factor">
                  U.S. EPA, eGRID2023 Summary Tables (rev. 2, June 2025): 770.9 lb CO₂e/MWh.
                </Item>
                <Item label="Equivalents">
                  U.S. EPA, Greenhouse Gas Equivalencies Calculator, calculations and references
                  (updated August 4, 2026).
                </Item>
                <Item label="Cloud AI">
                  Epoch AI, &ldquo;How much energy does ChatGPT use?&rdquo; (2025).
                </Item>
              </List>
              <p className="font-mono text-xs text-ink-min">Last reviewed: September 29, 2026.</p>
            </Section>
          </div>
        </div>
      </main>
      <Footer />
    </>
  );
}
