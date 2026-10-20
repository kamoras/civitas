const PARTY: Record<string, { label: string; text: string; border: string }> = {
  D: { label: "Democrat", text: "text-dem-blue", border: "border-dem-blue/40" },
  R: { label: "Republican", text: "text-signal-red", border: "border-signal-red/40" },
  DR: { label: "Democratic-Republican", text: "text-signal-cyan", border: "border-signal-cyan/40" },
  F: { label: "Federalist", text: "text-ind-purple", border: "border-ind-purple/40" },
  W: { label: "Whig", text: "text-signal-amber", border: "border-signal-amber/40" },
};
const NO_PARTY = { label: "No party", text: "text-ink", border: "border-white/30" };
// "U": a president who belonged to no party (George Washington).
PARTY.U = NO_PARTY;
// The API's code for the Democratic-Republicans (GET /api/presidents): read
// as "DR" alone, Jefferson, Madison, Monroe and J. Q. Adams showed "No party".
PARTY["D-R"] = PARTY.DR;

/** A president's party label and colours: the scorecard's, and the
 *  presidents list's (a plain module, so a server page can call it). */
export function presidentParty(code: string) {
  return PARTY[code] ?? NO_PARTY;
}
