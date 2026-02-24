"use client";

import { Suspense, useState } from "react";
import { useSearchParams } from "next/navigation";
import MatrixRain from "@/components/effects/MatrixRain";
import Navbar from "@/components/layout/Navbar";
import Footer from "@/components/layout/Footer";
import GlitchText from "@/components/effects/GlitchText";
import CheckerClient from "@/components/checker/CheckerClient";
import PresidentClient from "@/components/president/PresidentClient";
import BranchSelector, { type Branch } from "@/components/BranchSelector";
import ComingSoon from "@/components/ComingSoon";

function ScorecardContent() {
  const searchParams = useSearchParams();
  const initialBranch = (searchParams.get("branch") as Branch) || "senate";
  const [branch, setBranch] = useState<Branch>(initialBranch);

  return (
    <>
      <MatrixRain />
      <Navbar />
      <main id="main-content" className="pt-24 pb-16 px-4">
        <div className="max-w-7xl mx-auto">
          <div className="text-center mb-8">
            <GlitchText
              text="SCORECARD"
              as="h1"
              className="font-pixel text-xl sm:text-3xl md:text-4xl text-matrix-green animate-pulse-neon"
            />
          </div>

          <div className="mb-10">
            <BranchSelector selected={branch} onChange={setBranch} />
          </div>

          {branch === "senate" && (
            <Suspense fallback={null}>
              <CheckerClient />
            </Suspense>
          )}

          {branch === "president" && (
            <Suspense fallback={null}>
              <PresidentClient />
            </Suspense>
          )}

          {branch === "house" && <ComingSoon branch="house" />}

          {branch === "senate" && (
            <div className="text-center mt-16 mb-8">
              <div className="terminal-window max-w-lg mx-auto p-4">
                <p className="text-matrix-green/40 text-sm">
                  {">"} Select a branch above to view scorecards. House tracking
                  is coming soon. All public data. All free.
                </p>
              </div>
            </div>
          )}
        </div>
      </main>
      <Footer />
    </>
  );
}

export default function ScorecardPage() {
  return (
    <Suspense fallback={null}>
      <ScorecardContent />
    </Suspense>
  );
}
