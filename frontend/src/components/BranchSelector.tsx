"use client";

export type Branch = "senate" | "house" | "president";

const BRANCHES: { key: Branch; label: string }[] = [
  { key: "senate", label: "SENATE" },
  { key: "house", label: "HOUSE" },
  { key: "president", label: "PRESIDENT" },
];

interface BranchSelectorProps {
  selected: Branch;
  onChange: (branch: Branch) => void;
}

export default function BranchSelector({ selected, onChange }: BranchSelectorProps) {
  return (
    <div className="flex justify-center gap-1 sm:gap-2" role="tablist" aria-label="Government branch">
      {BRANCHES.map(({ key, label }) => (
        <button
          key={key}
          role="tab"
          aria-selected={selected === key}
          onClick={() => onChange(key)}
          className={`px-3 sm:px-5 py-2 text-xs sm:text-sm font-terminal border transition-all tracking-wider ${
            selected === key
              ? "bg-matrix-green/15 border-matrix-green text-matrix-green neon-green"
              : "border-white/10 text-white/50 hover:border-white/30 hover:text-white/70"
          }`}
        >
          {label}
        </button>
      ))}
    </div>
  );
}
