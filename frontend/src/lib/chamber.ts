const STYLES: Record<string, { color: string; border: string; bg: string }> = {
  Senate: { color: "text-neon-cyan", border: "border-neon-cyan/30", bg: "border-neon-cyan/30 bg-neon-cyan/5" },
  House: { color: "text-neon-pink", border: "border-neon-pink/30", bg: "border-neon-pink/30 bg-neon-pink/5" },
  Executive: { color: "text-neon-yellow", border: "border-neon-yellow/30", bg: "border-neon-yellow/30 bg-neon-yellow/5" },
  Judicial: { color: "text-purple-400", border: "border-purple-400/30", bg: "border-purple-400/30 bg-purple-400/5" },
  Regulatory: { color: "text-orange-400", border: "border-orange-400/30", bg: "border-orange-400/30 bg-orange-400/5" },
};

const DEFAULTS = { color: "text-matrix-green/60", border: "border-matrix-green/20", bg: "border-matrix-green/20 bg-matrix-green/5" };

export function chamberColor(chamber: string): string {
  return (STYLES[chamber] ?? DEFAULTS).color;
}

export function chamberBorder(chamber: string): string {
  return (STYLES[chamber] ?? DEFAULTS).border;
}

export function chamberBg(chamber: string): string {
  return (STYLES[chamber] ?? DEFAULTS).bg;
}

export function chamberLabel(chamber: string): string {
  if (chamber === "Regulatory") return "AGENCY";
  return chamber?.toUpperCase() || "GOV";
}
