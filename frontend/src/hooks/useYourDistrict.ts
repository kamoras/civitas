import { useCallback } from "react";
import { useSessionValue } from "@/hooks/useSessionToken";
import { LOOKUP_LINES } from "@/lib/districtAt";
import { STATE_CODES } from "@/lib/stateCodes";

/** The sessionStorage key for the reader's House district, this tab only. */
export const YOUR_DISTRICT_KEY = "civitas:your-district";

const KNOWN = new Set<string>(STATE_CODES);

/** A House district and the map it is on: a number means nothing without
 * its lines, since a state that redrew reuses every number. */
export interface YourDistrict {
  state: string;
  lines: string;
  house: number;
}

function parse(raw: string | null): YourDistrict | null {
  try {
    const v = JSON.parse(raw ?? "null");
    return v &&
      KNOWN.has(v.state) &&
      typeof v.lines === "string" &&
      LOOKUP_LINES.test(v.lines) &&
      Number.isInteger(v.house) &&
      v.house >= 0
      ? { state: v.state, lines: v.lines, house: v.house }
      : null;
  } catch {
    return null;
  }
}

/**
 * The House district the reader found (by location, a tap on a town's map,
 * or a pick from its list), for the open tab only (AGENTS.md §8):
 * sessionStorage, gone when the tab closes, never sent anywhere. Only the
 * state, the map and the district number are kept, never a position.
 */
export function useYourDistrict(): {
  district: YourDistrict | null;
  setDistrict: (d: YourDistrict | null) => void;
} {
  const { value, set } = useSessionValue(YOUR_DISTRICT_KEY);
  const setDistrict = useCallback(
    (d: YourDistrict | null) => {
      const ok = parse(JSON.stringify(d));
      set(ok ? JSON.stringify(ok) : null);
    },
    [set]
  );
  return { district: parse(value), setDistrict };
}
