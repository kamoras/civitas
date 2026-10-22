export function chamberLabel(chamber: string): string {
  if (chamber === "Regulatory") return "Agency";
  return chamber || "Gov";
}
