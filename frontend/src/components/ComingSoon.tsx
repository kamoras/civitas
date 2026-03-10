"use client";

import TerminalTitlebar from "@/components/TerminalTitlebar";

type ComingSoonBranch = "house";

const COPY: Record<ComingSoonBranch, { title: string; lines: string[] }> = {
  house: {
    title: "HOUSE OF REPRESENTATIVES",
    lines: [
      "435 members. 435 funding profiles.",
      "District-level campaign finance data from FEC filings.",
      "Voting records cross-referenced with donor interests.",
      "We're building it now.",
    ],
  },
};

export default function ComingSoon({ branch }: { branch: ComingSoonBranch }) {
  const { title, lines } = COPY[branch];

  return (
    <div className="flex flex-col items-center justify-center py-20 px-4">
      <div className="terminal-window max-w-lg w-full">
        <TerminalTitlebar title={`${branch}_tracker.exe`} />
        <div className="p-6 sm:p-8 text-center space-y-6">
          <div className="text-neon-pink font-pixel text-xs sm:text-sm tracking-widest">
            {title}
          </div>

          <div className="space-y-1 text-sm text-matrix-green/60">
            {lines.map((line, i) => (
              <p key={i}>{">"} {line}</p>
            ))}
          </div>

          <div className="border border-neon-yellow/30 bg-neon-yellow/5 px-4 py-2 inline-block">
            <span className="text-neon-yellow text-sm font-terminal tracking-wider animate-pulse">
              [ COMING SOON ]
            </span>
          </div>

          <p className="text-matrix-green/25 text-xs">
            All data will be sourced from public federal records.
          </p>
        </div>
      </div>
    </div>
  );
}
