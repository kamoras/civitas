const STYLES: Record<string, { color: string; border: string }> = {
  Senate: {
    color: "text-signal-cyan",
    border: "border-white/15",
  },
  House: {
    color: "text-signal-magenta",
    border: "border-signal-magenta/40",
  },
  Executive: {
    color: "text-signal-amber",
    border: "border-signal-amber/40",
  },
  Judicial: {
    color: "text-ind-purple",
    border: "border-ind-purple/40",
  },
  Regulatory: {
    color: "text-signal-orange",
    border: "border-signal-orange/40",
  },
};

const DEFAULTS = {
  color: "text-ink-lo",
  border: "border-white/[0.07]",
};

export function chamberColor(chamber: string): string {
  return (STYLES[chamber] ?? DEFAULTS).color;
}

export function chamberBorder(chamber: string): string {
  return (STYLES[chamber] ?? DEFAULTS).border;
}

export function chamberLabel(chamber: string): string {
  if (chamber === "Regulatory") return "Agency";
  return chamber || "Gov";
}
