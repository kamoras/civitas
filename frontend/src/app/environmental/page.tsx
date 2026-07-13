import type { Metadata } from "next";
import MatrixRain from "@/components/effects/MatrixRain";
import Navbar from "@/components/layout/Navbar";
import Footer from "@/components/layout/Footer";
import TerminalTitlebar from "@/components/TerminalTitlebar";

export const metadata: Metadata = {
  title: "Environmental Impact — Civitas",
  description:
    "Civitas runs on a Raspberry Pi 5 drawing ~7W. Here is an honest accounting of our energy use, carbon footprint, and why local AI infrastructure matters.",
  openGraph: {
    title: "Environmental Impact — Civitas",
    description:
      "Civitas runs on ~61 kWh per year — less energy than ChatGPT uses in 11 seconds. An honest look at our infrastructure footprint.",
  },
};

function Section({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section className="terminal-window mb-6" aria-label={title}>
      <TerminalTitlebar
        title={title.toLowerCase().replace(/ /g, "_") + ".txt"}
      />
      <div className="p-6 space-y-4">
        <h2 className="text-neon-cyan font-terminal text-sm tracking-widest">
          {title}
        </h2>
        {children}
      </div>
    </section>
  );
}

function P({ children }: { children: React.ReactNode }) {
  return (
    <p className="text-sm text-matrix-green/70 leading-relaxed">{children}</p>
  );
}

function Stat({
  value,
  label,
  sub,
}: {
  value: string;
  label: string;
  sub?: string;
}) {
  return (
    <div className="border border-matrix-green/20 p-4 text-center">
      <div className="font-pixel text-matrix-green text-lg sm:text-2xl mb-1">
        {value}
      </div>
      <div className="text-matrix-green/60 text-xs font-terminal tracking-wider uppercase">
        {label}
      </div>
      {sub && (
        <div className="text-matrix-green/35 text-[10px] font-terminal mt-1">
          {sub}
        </div>
      )}
    </div>
  );
}

function Row({
  label,
  value,
  note,
  highlight,
}: {
  label: string;
  value: string;
  note?: string;
  highlight?: boolean;
}) {
  return (
    <div className="flex flex-col sm:flex-row sm:items-baseline gap-1 sm:gap-4 py-2 border-b border-matrix-green/10 last:border-0">
      <span className="text-matrix-green/50 text-xs font-terminal tracking-wider min-w-[160px]">
        {label}
      </span>
      <span
        className={`text-sm font-terminal ${highlight ? "text-neon-cyan" : "text-matrix-green"}`}
      >
        {value}
      </span>
      {note && (
        <span className="text-matrix-green/35 text-xs font-terminal">{note}</span>
      )}
    </div>
  );
}

function Caveat({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex gap-3 py-2 border-b border-matrix-green/10 last:border-0">
      <span className="text-neon-pink/60 text-xs font-terminal shrink-0 mt-0.5">
        ⚠
      </span>
      <p className="text-sm text-matrix-green/60 leading-relaxed">{children}</p>
    </div>
  );
}

export default function EnvironmentalPage() {
  return (
    <>
      <MatrixRain />
      <Navbar />
      <main id="main-content" tabIndex={-1} className="pt-24 pb-16 px-4">
        <div className="max-w-3xl mx-auto">

          {/* Header */}
          <div className="text-center mb-10">
            <h1 className="font-pixel text-lg sm:text-2xl text-matrix-green tracking-widest mb-3">
              ENVIRONMENTAL IMPACT
            </h1>
            <p className="text-matrix-green/40 text-sm max-w-xl mx-auto leading-relaxed">
              As an AI-powered application, we believe you have a right to know
              what our infrastructure costs the planet. This page is our attempt
              at an honest answer.
            </p>
          </div>

          {/* Headline stats */}
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-8">
            <Stat
              value="~61 kWh"
              label="Annual energy"
              sub="entire site, all ops"
            />
            <Stat
              value="~7 W"
              label="Average draw"
              sub="Raspberry Pi 5"
            />
            <Stat
              value="~23.5 kg"
              label="CO₂e / year"
              sub="US grid average"
            />
            <Stat
              value="0"
              label="Cloud AI calls"
              sub="100% local inference"
            />
          </div>

          {/* Infrastructure */}
          <Section title="OUR INFRASTRUCTURE">
            <P>
              Civitas runs entirely on a single Raspberry Pi 5 — a credit-card-sized
              computer with a 4-core ARM Cortex-A76 processor drawing 3–11 watts
              depending on load. There is no cloud provider, no managed Kubernetes
              cluster, no CDN replication across dozens of edge nodes.
            </P>
            <div className="space-y-0 mt-2">
              <Row label="Hardware" value="Raspberry Pi 5 (BCM2712)" />
              <Row label="CPU architecture" value="ARM Cortex-A76 (64-bit RISC)" note="~5–10× more efficient per watt than equivalent x86 server silicon" />
              <Row label="AI inference" value="Ollama (local)" note="runs on-device — no data leaves the machine" />
              <Row label="Web server" value="Next.js (self-hosted)" />
              <Row label="API" value="FastAPI (Python)" />
              <Row label="Database" value="SQLite + Chroma (local)" />
              <Row label="Hosting provider" value="None" note="physically co-located with the developer" />
              <Row label="User data stored" value="None" note="no accounts, no tracking, no behavioral profiles" />
            </div>
          </Section>

          {/* Energy */}
          <Section title="ENERGY USE">
            <P>
              Measured with a USB-C inline power meter, the Pi 5 draws approximately
              3 W at idle and peaks around 10–11 W during the nightly pipeline.
              We use 7 W as our stated operating average — conservative but defensible
              across idle periods, web serving, and inference runs.
            </P>
            <div className="space-y-0 mt-2">
              <Row label="Idle draw" value="~3.0 W" note="headless, no active inference" />
              <Row label="Web serving" value="~4–5 W" />
              <Row label="Nightly pipeline (LLM)" value="~8–11 W" note="runs ~6–8h each night" />
              <Row label="Stated average" value="~7 W" highlight />
              <Row label="Annual consumption" value="~61 kWh" highlight note="7W × 24h × 365 days" />
            </div>
            <div className="mt-4 p-3 border border-matrix-green/20 bg-matrix-green/5">
              <p className="text-xs text-matrix-green/60 font-terminal leading-relaxed">
                For context: a typical US refrigerator uses 400–500 kWh/year.
                Civitas consumes roughly one-seventh the energy of keeping your
                food cold.
              </p>
            </div>
          </Section>

          {/* Carbon */}
          <Section title="CARBON FOOTPRINT">
            <P>
              We calculate our carbon footprint using the EPA eGRID 2024 national
              average grid intensity of 0.384 kg CO₂e per kWh. We do not know
              the exact energy mix of our ISP or the local grid — we use the
              national average as the honest, conservative estimate.
            </P>
            <div className="space-y-0 mt-2">
              <Row label="Grid intensity (US avg)" value="0.384 kg CO₂e / kWh" note="EPA eGRID 2024" />
              <Row label="Annual footprint" value="~23.5 kg CO₂e" highlight note="61.3 kWh × 0.384" />
              <Row label="Equivalent driving" value="~95 miles" note="at EPA average 0.25 kg CO₂/mile" />
              <Row label="Equivalent phone charges" value="~2,400" />
              <Row label="UK grid (for reference)" value="~10.9 kg CO₂e / yr" note="UK DESNZ 2025: 0.177 kg/kWh — higher renewables share" />
            </div>
          </Section>

          {/* vs cloud AI */}
          <Section title="WHY LOCAL AI MATTERS">
            <P>
              Every AI application makes an infrastructure choice. We chose to run
              a small language model locally on low-power ARM hardware rather than
              sending queries to cloud AI APIs. Here is what that means in practice.
            </P>
            <div className="overflow-x-auto mt-2">
              <table className="w-full text-xs font-terminal border-collapse">
                <thead>
                  <tr className="border-b border-matrix-green/20">
                    <th className="text-left text-matrix-green/50 py-2 pr-4 font-normal tracking-wider">METRIC</th>
                    <th className="text-right text-neon-pink/70 py-2 pr-4 font-normal tracking-wider">CLOUD AI (GPT-4o)</th>
                    <th className="text-right text-matrix-green py-2 font-normal tracking-wider">CIVITAS (LOCAL)</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-matrix-green/10">
                  <tr>
                    <td className="text-matrix-green/50 py-2 pr-4">Hardware power draw</td>
                    <td className="text-right text-matrix-green/70 pr-4">~700 W per H100 GPU</td>
                    <td className="text-right text-matrix-green">~10 W (Pi 5)</td>
                  </tr>
                  <tr>
                    <td className="text-matrix-green/50 py-2 pr-4">Energy per inference</td>
                    <td className="text-right text-matrix-green/70 pr-4">~0.30 Wh</td>
                    <td className="text-right text-matrix-green">~0.15 Wh</td>
                  </tr>
                  <tr>
                    <td className="text-matrix-green/50 py-2 pr-4">Data center overhead (PUE)</td>
                    <td className="text-right text-matrix-green/70 pr-4">1.15× multiplier</td>
                    <td className="text-right text-matrix-green">None</td>
                  </tr>
                  <tr>
                    <td className="text-matrix-green/50 py-2 pr-4">Network round-trip to GPU</td>
                    <td className="text-right text-matrix-green/70 pr-4">Yes (internet)</td>
                    <td className="text-right text-matrix-green">No (localhost)</td>
                  </tr>
                  <tr>
                    <td className="text-matrix-green/50 py-2 pr-4">Query data leaves device</td>
                    <td className="text-right text-matrix-green/70 pr-4">Yes</td>
                    <td className="text-right text-matrix-green">No</td>
                  </tr>
                  <tr>
                    <td className="text-matrix-green/50 py-2 pr-4">Pipeline AI energy / year</td>
                    <td className="text-right text-matrix-green/70 pr-4">~55 kWh (est.)</td>
                    <td className="text-right text-matrix-green">included in 61 kWh total</td>
                  </tr>
                </tbody>
              </table>
            </div>
            <div className="mt-4 p-3 border border-neon-cyan/20 bg-neon-cyan/5">
              <p className="text-xs text-neon-cyan/70 font-terminal leading-relaxed">
                ChatGPT serves ~700 million users weekly and is estimated to consume
                340+ MWh of electricity per day for inference alone. Civitas&apos;s
                entire annual energy budget — 61 kWh — is consumed by ChatGPT in
                roughly <span className="text-neon-cyan font-bold">11 seconds</span>.
              </p>
            </div>
          </Section>

          {/* Design choices */}
          <Section title="DESIGN CHOICES THAT REDUCE IMPACT">
            <div className="space-y-3">
              {[
                {
                  title: "No persistent user data",
                  body: "Civitas stores no user accounts, behavioral profiles, or tracking data. No analytics databases, no session replication, no data pipelines churning user signals 24/7.",
                },
                {
                  title: "No CDN edge replication",
                  body: "A typical production web app replicates assets across 30–100+ CDN edge nodes globally. Civitas serves content from a single machine. If you're in Seoul, you're hitting the Pi. That's a deliberate trade-off.",
                },
                {
                  title: "ARM efficiency over x86 throughput",
                  body: "The Cortex-A76 core in the Pi 5 runs at a TDP of ~3W per core. A comparable Intel Xeon core at the same process node consumes 5–10× more. ARM's RISC architecture is inherently more efficient for the steady-state serving workloads Civitas runs.",
                },
                {
                  title: "Single-device consolidation",
                  body: "Web server, API, database, and LLM inference all run on one physical device drawing 7W total. A cloud-equivalent architecture typically involves 3–5 separate service instances plus managed databases, with a combined idle baseline of 50–100W before data center overhead.",
                },
                {
                  title: "Local inference = no data center transit",
                  body: "Each cloud AI query traverses public internet routing, BGP peering, CDN edges, and data center networking before reaching a GPU. Local Ollama inference happens entirely on-device — the energy cost of network transit is zero.",
                },
              ].map(({ title, body }) => (
                <div
                  key={title}
                  className="border-l-2 border-matrix-green/30 pl-4"
                >
                  <p className="text-xs font-terminal text-matrix-green/80 tracking-wider mb-1">
                    {title.toUpperCase()}
                  </p>
                  <p className="text-sm text-matrix-green/60 leading-relaxed">
                    {body}
                  </p>
                </div>
              ))}
            </div>
          </Section>

          {/* Honest caveats */}
          <Section title="WHAT WE CANNOT CLAIM">
            <P>
              Transparency requires acknowledging what we don&apos;t know and what
              we haven&apos;t measured.
            </P>
            <div className="mt-2 space-y-0">
              <Caveat>
                <strong className="text-matrix-green/80">Our ISP&apos;s energy mix is unknown.</strong>{" "}
                The 7W figure covers the Pi only. Modem, router, upstream fiber
                infrastructure, and internet backbone all consume power we cannot
                attribute or measure. Networking infrastructure adds an estimated
                0.06 kWh per GB transferred (IEA), but that cost is diffuse and shared.
              </Caveat>
              <Caveat>
                <strong className="text-matrix-green/80">Accessories are not counted.</strong>{" "}
                The real system idle — including router, modem, and power supply
                losses — is likely 12–20W, not 7W.
              </Caveat>
              <Caveat>
                <strong className="text-matrix-green/80">We don&apos;t hold renewable energy certificates.</strong>{" "}
                Without a PPA or REC, we use the US national average grid intensity.
                If the Pi is on a cleaner regional grid (e.g., Pacific Northwest hydro
                at ~0.08 kg CO₂e/kWh), the real footprint could be 4× lower — but
                we don&apos;t claim that.
              </Caveat>
              <Caveat>
                <strong className="text-matrix-green/80">Embodied carbon is not included.</strong>{" "}
                Manufacturing a Raspberry Pi 5 has a carbon cost. Raspberry Pi Ltd.
                does not publish lifecycle assessment data. We estimate 10–30 kg CO₂e
                for manufacturing based on comparable devices, amortized over a 5-year
                lifespan.
              </Caveat>
              <Caveat>
                <strong className="text-matrix-green/80">Inference energy varies by model and query length.</strong>{" "}
                Longer prompts, larger models, and unquantized weights all increase
                energy per inference. Our 0.15 Wh estimate assumes a quantized
                sub-7B model on typical short civic queries.
              </Caveat>
            </div>
          </Section>

          {/* Sources */}
          <Section title="DATA SOURCES">
            <div className="space-y-1 text-xs font-terminal text-matrix-green/50">
              {[
                "EPA eGRID 2024 — US grid average carbon intensity (0.384 kg CO₂e/kWh)",
                "UK DESNZ 2025 — UK grid emissions factor (0.177 kg CO₂e/kWh)",
                "Epoch AI (2024) — GPT-4o energy per inference (~0.30 Wh)",
                "raspberry.tips (2026) — Pi 5 measured power consumption (USB-C inline meter)",
                "Arxiv 2511.07425 — LLM inference on single-board computers (Pi 5 ~10W under load)",
                "Amazon Sustainability Report 2024 — AWS global data center PUE of 1.15",
                "IEA (2023) — Network energy intensity estimate (0.06 kWh/GB)",
                "EPA (2024) — Passenger vehicle CO₂ per mile (~0.25 kg CO₂/mile average)",
              ].map((s) => (
                <p key={s} className="before:content-['›'] before:mr-2 before:text-matrix-green/30">
                  {s}
                </p>
              ))}
            </div>
            <p className="text-xs text-matrix-green/35 font-terminal mt-4 leading-relaxed">
              Energy and carbon figures are estimates based on published data. We
              update this page when better measurements become available. Last
              reviewed: June 2026.
            </p>
          </Section>

        </div>
      </main>
      <Footer />
    </>
  );
}
