import MatrixRain from "@/components/effects/MatrixRain";
import Navbar from "@/components/layout/Navbar";
import Footer from "@/components/layout/Footer";
import GlitchText from "@/components/effects/GlitchText";
import CheckerClient from "@/components/checker/CheckerClient";

export const metadata = {
  title: "CORPORATE INFLUENCE TRACKER // MODERN PUNK",
  description:
    "Select your state. See who funds your senators, how they vote, and where the lobbying money goes. All public data.",
};

export default function InfluenceTrackerPage() {
  return (
    <>
      <MatrixRain />
      <Navbar />
      <main className="pt-24 pb-16 px-4">
        <div className="max-w-7xl mx-auto">
          <div className="text-center mb-12">
            <GlitchText
              text="CORPORATE INFLUENCE TRACKER"
              as="h1"
              className="font-pixel text-xl sm:text-3xl md:text-4xl text-matrix-green animate-pulse-neon"
            />
          </div>

          <CheckerClient />

          <div className="text-center mt-16 mb-8">
            <div className="terminal-window max-w-lg mx-auto p-4">
              <p className="text-matrix-green/40 text-sm">
                {">"} This is the first Modern Punk tool. We&apos;re building more: House Rep
                tracking, lobbying timelines, and corporate influence scorecards. All public data.
                All free.
              </p>
            </div>
          </div>
        </div>
      </main>
      <Footer />
    </>
  );
}
