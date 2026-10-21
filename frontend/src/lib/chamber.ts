const STYLES: Record<string, { color: string; bg: string }> = {
  Senate: {
    color: "text-signal-cyan",
    bg: "border-white/15 bg-signal-cyan/10",
  },
  House: {
    color: "text-signal-magenta",
    bg: "border-signal-magenta/40 bg-signal-magenta/10",
  },
  Executive: {
    color: "text-signal-amber",
    bg: "border-signal-amber/40 bg-signal-amber/10",
  },
  Judicial: {
    color: "text-ind-purple",
    bg: "border-ind-purple/40 bg-ind-purple/10",
  },
  Regulatory: {
    color: "text-signal-orange",
    bg: "border-signal-orange/40 bg-signal-orange/10",
  },
};

const DEFAULTS = {
  color: "text-ink-lo",
  bg: "border-white/[0.07] bg-white/[0.03]",
};

export function chamberColor(chamber: string): string {
  return (STYLES[chamber] ?? DEFAULTS).color;
}

export function chamberBg(chamber: string): string {
  return (STYLES[chamber] ?? DEFAULTS).bg;
}

export function chamberLabel(chamber: string): string {
  if (chamber === "Regulatory") return "Agency";
  return chamber || "Gov";
}
