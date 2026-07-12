"use client";

import Link from "next/link";
import Navbar from "@/components/layout/Navbar";
import MatrixRain from "@/components/effects/MatrixRain";
import Footer from "@/components/layout/Footer";
import BackToTop from "@/components/BackToTop";
import SenatorCard from "@/components/checker/SenatorCard";
import { PresidentCard } from "@/components/president/PresidentClient";
import { JusticeCard } from "@/components/justice/JusticeClient";
import type { PoliticianProfile } from "@/types/politicians";
import type { Senator } from "@/types/senator";
import type { President } from "@/types/president";
import type { Justice } from "@/types/justice";

function branchLabel(branch: string) {
  const map: Record<string, string> = {
    senate: "SENATE",
    house: "HOUSE",
    president: "EXECUTIVE",
    scotus: "JUDICIAL",
  };
  return map[branch] ?? branch.toUpperCase();
}

export default function FullScorecardClient({ profile }: { profile: PoliticianProfile }) {
  const { identity, branch, hasScorecard, scorecard } = profile;

  return (
    <div className="min-h-screen bg-crt-black text-matrix-green">
      <MatrixRain />
      <Navbar />
      <main id="main-content" tabIndex={-1} className="pt-24 pb-16 px-4">
        <div className="max-w-4xl mx-auto">

          {/* Breadcrumb */}
          <div className="mb-6 font-mono text-[10px] text-matrix-green/30">
            <Link href="/politicians" className="hover:text-matrix-green/60 transition-colors">
              ← POLITICIANS
            </Link>
            <span className="mx-2">/</span>
            <Link href={`/politicians/${profile.id}`} className="hover:text-matrix-green/60 transition-colors">
              {identity.name}
            </Link>
            <span className="mx-2">/</span>
            <span className="text-matrix-green/50">FULL SCORECARD · {branchLabel(branch)}</span>
          </div>

          {!hasScorecard || !scorecard ? (
            <div className="border border-matrix-green/20 bg-crt-black/40 p-8 text-center">
              <p className="font-mono text-xs text-matrix-green/30 tracking-widest">
                SCORECARD NOT YET GENERATED — CHECK BACK AFTER NEXT PIPELINE RUN
              </p>
            </div>
          ) : (
            <div className="max-w-3xl mx-auto">
              {branch === "senate" && (
                <SenatorCard senator={scorecard as unknown as Senator} chamber="senate" />
              )}
              {branch === "house" && (
                <SenatorCard senator={scorecard as unknown as Senator} chamber="house" />
              )}
              {branch === "president" && (
                <PresidentCard president={scorecard as unknown as President} />
              )}
              {branch === "scotus" && (
                <JusticeCard justice={scorecard as unknown as Justice} />
              )}

              {(branch === "senate" || branch === "house") && identity.state && (
                <div className="mt-4 text-center">
                  <Link
                    href={`/scorecard?branch=${branch}&state=${identity.state}`}
                    className="font-mono text-[10px] text-matrix-green/35 hover:text-matrix-green/60 transition-colors tracking-widest"
                  >
                    COMPARE ALL {identity.stateName ?? identity.state} {branch === "senate" ? "SENATORS" : "REPRESENTATIVES"} →
                  </Link>
                </div>
              )}
            </div>
          )}

        </div>
      </main>
      <BackToTop />
      <Footer />
    </div>
  );
}
