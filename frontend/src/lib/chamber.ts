const STYLES: Record<string, { color: string }> = {
  Senate: {
    color: "text-signal-cyan",
  },
  House: {
    color: "text-signal-magenta",
  },
  Executive: {
    color: "text-signal-amber",
  },
  Judicial: {
    color: "text-ind-purple",
  },
  Regulatory: {
    color: "text-signal-orange",
  },
};

const DEFAULTS = {
  color: "text-ink-lo",
};

export function chamberColor(chamber: string): string {
  return (STYLES[chamber] ?? DEFAULTS).color;
}

export function chamberLabel(chamber: string): string {
  if (chamber === "Regulatory") return "Agency";
  return chamber || "Gov";
}
