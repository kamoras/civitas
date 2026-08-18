"use client";

import { useCallback } from "react";

export type Branch = "senate" | "house" | "president" | "scotus";

const BRANCHES: { key: Branch; label: string }[] = [
  { key: "senate", label: "SENATE" },
  { key: "house", label: "HOUSE" },
  { key: "president", label: "PRESIDENT" },
  { key: "scotus", label: "SCOTUS" },
];

interface BranchSelectorProps {
  selected: Branch;
  onChange: (branch: Branch) => void;
}

export default function BranchSelector({ selected, onChange }: BranchSelectorProps) {
  const onKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      const idx = BRANCHES.findIndex((b) => b.key === selected);
      let next = idx;
      if (e.key === "ArrowRight" || e.key === "ArrowDown") {
        next = (idx + 1) % BRANCHES.length;
      } else if (e.key === "ArrowLeft" || e.key === "ArrowUp") {
        next = (idx - 1 + BRANCHES.length) % BRANCHES.length;
      } else if (e.key === "Home") {
        next = 0;
      } else if (e.key === "End") {
        next = BRANCHES.length - 1;
      } else {
        return;
      }
      e.preventDefault();
      onChange(BRANCHES[next].key);
      requestAnimationFrame(() => {
        document.getElementById(`branch-panel-${BRANCHES[next].key}`)?.focus();
      });
    },
    [selected, onChange]
  );

  return (
    <div
      className="flex justify-center gap-1 sm:gap-2"
      role="tablist"
      aria-label="Government branch"
      onKeyDown={onKeyDown}
    >
      {BRANCHES.map(({ key, label }) => (
        <button
          key={key}
          role="tab"
          id={`branch-tab-${key}`}
          aria-selected={selected === key}
          aria-controls={`branch-panel-${key}`}
          tabIndex={selected === key ? 0 : -1}
          onClick={() => onChange(key)}
          className={`px-3 sm:px-5 py-2 text-xs sm:text-sm font-mono border transition-all tracking-wider ${
            selected === key
              ? "bg-phos border-phos text-surface-base"
              : "border-white/[0.07] text-ink-lo hover:border-white/30 hover:text-ink"
          }`}
        >
          {label}
        </button>
      ))}
    </div>
  );
}
