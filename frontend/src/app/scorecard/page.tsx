"use client";

import { Suspense, useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import MatrixRain from "@/components/effects/MatrixRain";
import Navbar from "@/components/layout/Navbar";
import Footer from "@/components/layout/Footer";
import GlitchText from "@/components/effects/GlitchText";
import CheckerClient from "@/components/checker/CheckerClient";
import PresidentClient from "@/components/president/PresidentClient";
import JusticeClient from "@/components/justice/JusticeClient";
import HouseCheckerClient from "@/components/checker/HouseCheckerClient";
import BranchSelector, { type Branch } from "@/components/BranchSelector";
import { useUserState } from "@/hooks/useUserState";

function ScorecardContent() {
  const searchParams = useSearchParams();
  const router = useRouter();
  const initialBranch = (searchParams.get("branch") as Branch) || "senate";
  const [branch, setBranchState] = useState<Branch>(initialBranch);
  const [savedState] = useUserState();

  // Pre-populate the state URL param from localStorage when no ?state= param is present
  useEffect(() => {
    if (!searchParams.get("state") && savedState) {
      const params = new URLSearchParams(searchParams.toString());
      params.set("state", savedState);
      router.replace(`?${params.toString()}`, { scroll: false });
    }
  }, [savedState]); // eslint-disable-line react-hooks/exhaustive-deps

  const setBranch = useCallback((b: Branch) => {
    setBranchState(b);
    const url = new URL(window.location.href);
    url.searchParams.set("branch", b);
    window.history.replaceState({}, "", url.toString());
  }, []);

  return (
    <>
      <MatrixRain />
      <Navbar />
      <main id="main-content" tabIndex={-1} className="pt-24 pb-16 px-4">
        <div className="max-w-7xl mx-auto">
          <div className="text-center mb-8">
            <h1 className="font-pixel text-xl sm:text-3xl md:text-4xl text-matrix-green neon-green">SCORECARD</h1>
          </div>

          <div className="mb-10">
            <BranchSelector selected={branch} onChange={setBranch} />
            <div className="text-center mt-3">
              <Link
                href={`/leaderboard?branch=${branch}`}
                className="font-mono text-[10px] tracking-widest text-matrix-green/35 hover:text-matrix-green/70 transition-colors"
              >
                VIEW FULL LEADERBOARD →
              </Link>
            </div>
          </div>

          <div id={`branch-panel-${branch}`} role="tabpanel" aria-labelledby={`branch-tab-${branch}`} tabIndex={-1}>
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

            {branch === "scotus" && (
              <Suspense fallback={null}>
                <JusticeClient />
              </Suspense>
            )}

            {branch === "house" && (
              <Suspense fallback={null}>
                <HouseCheckerClient />
              </Suspense>
            )}
          </div>
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
