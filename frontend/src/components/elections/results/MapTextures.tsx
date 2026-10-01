"use client";

import { useId } from "react";
import {
  AWAITING_FILL,
  AWAITING_MARK,
  FEED_FAILED_FILL,
  FEED_FAILED_MARK,
  NO_COUNT_FILL,
  NO_COUNT_STRIPE,
  POLLS_OPEN_FILL,
  POLLS_OPEN_MARK,
  STALE_SHADOW,
} from "@/lib/results";

/**
 * The textures that set apart the dark, count-less fills on a results map
 * (see POLLS_OPEN_MARK in lib/results). `defs` goes inside the map's <svg>;
 * `paint` turns a fill from stateShade/resultFill into the textured one
 * where it has one. Ids are per map, so two maps on one page (a state
 * page's count and its research drawer) never share one.
 *
 * `staleFills` are the fills (as stateShade/resultFill give them) that some
 * shape on the map draws as a stale count: each gets a pattern of that fill
 * under the amber-and-dark stale stripe (STALE_SWATCH in lib/results), and
 * `paint(fill, true)` returns it. An SVG pattern can't take its base colour
 * from the shape using it, hence one per fill.
 */
export function useMapTextures(staleFills: readonly string[] = []): {
  defs: React.ReactNode;
  paint: (fill: string, stale?: boolean) => string;
} {
  const base = `tex-${useId().replace(/[^a-zA-Z0-9_-]/g, "")}`;
  const ids = {
    polls: `${base}-polls`,
    awaiting: `${base}-awaiting`,
    feed: `${base}-feed`,
    noCount: `${base}-nocount`,
  };
  const stale = [...new Set(staleFills)];
  const staleId = (fill: string) => `${base}-stale-${stale.indexOf(fill)}`;
  const defs = (
    <defs>
      <pattern
        id={ids.polls}
        patternUnits="userSpaceOnUse"
        width="6"
        height="6"
        patternTransform="rotate(45)"
      >
        <rect width="6" height="6" fill={POLLS_OPEN_FILL} />
        <rect width="2" height="6" fill={POLLS_OPEN_MARK} />
      </pattern>
      <pattern
        id={ids.feed}
        patternUnits="userSpaceOnUse"
        width="6"
        height="6"
        patternTransform="rotate(-45)"
      >
        <rect width="6" height="6" fill={FEED_FAILED_FILL} />
        <rect width="2" height="6" fill={FEED_FAILED_MARK} />
      </pattern>
      {/* No count shown here: a district (DistrictMap) or a whole
          chamber (the national map) with no count while the state's others
          have one. Hatched the other way from the amber stripes. */}
      <pattern
        id={ids.noCount}
        patternUnits="userSpaceOnUse"
        width="6"
        height="6"
        patternTransform="rotate(45)"
      >
        <rect width="6" height="6" fill={AWAITING_FILL} />
        <rect width="2" height="6" fill={NO_COUNT_STRIPE} />
      </pattern>
      <pattern id={ids.awaiting} patternUnits="userSpaceOnUse" width="5" height="5">
        <rect width="5" height="5" fill={AWAITING_FILL} />
        <circle cx="2.5" cy="2.5" r="1" fill={AWAITING_MARK} />
      </pattern>
      {stale.map((fill) => (
        <pattern
          key={fill}
          id={staleId(fill)}
          patternUnits="userSpaceOnUse"
          width="6"
          height="6"
          patternTransform="rotate(-45)"
        >
          {/* The map's own background under a translucent fill, as the
              shape would be drawn without the pattern. */}
          <rect width="6" height="6" fill="#14110e" />
          <rect width="6" height="6" fill={fill} />
          <rect width="1.5" height="6" fill={FEED_FAILED_MARK} />
          <rect x="1.5" width="1.5" height="6" fill={STALE_SHADOW} />
        </pattern>
      ))}
    </defs>
  );
  const paint = (fill: string, isStale = false) =>
    isStale && stale.includes(fill)
      ? `url(#${staleId(fill)})`
      : fill === POLLS_OPEN_FILL
        ? `url(#${ids.polls})`
        : fill === AWAITING_FILL
          ? `url(#${ids.awaiting})`
          : fill === NO_COUNT_FILL
            ? `url(#${ids.noCount})`
            : fill === FEED_FAILED_FILL
              ? `url(#${ids.feed})`
              : fill;
  return { defs, paint };
}
