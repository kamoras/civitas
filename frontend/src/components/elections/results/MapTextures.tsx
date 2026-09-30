"use client";

import { useId } from "react";
import {
  AWAITING_FILL,
  AWAITING_MARK,
  FEED_FAILED_FILL,
  FEED_FAILED_MARK,
  POLLS_OPEN_FILL,
  POLLS_OPEN_MARK,
} from "@/lib/results";

/**
 * The textures that set apart the dark, count-less fills on a results map
 * (see POLLS_OPEN_MARK in lib/results). `defs` goes inside the map's <svg>;
 * `paint` turns a fill from stateShade/resultFill into the textured one
 * where it has one. Ids are per map, so two maps on one page (a state
 * page's count and its research drawer) never share one.
 */
export function useMapTextures(): { defs: React.ReactNode; paint: (fill: string) => string } {
  const base = `tex-${useId().replace(/[^a-zA-Z0-9_-]/g, "")}`;
  const ids = {
    polls: `${base}-polls`,
    awaiting: `${base}-awaiting`,
    feed: `${base}-feed`,
  };
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
      <pattern id={ids.awaiting} patternUnits="userSpaceOnUse" width="5" height="5">
        <rect width="5" height="5" fill={AWAITING_FILL} />
        <circle cx="2.5" cy="2.5" r="1" fill={AWAITING_MARK} />
      </pattern>
    </defs>
  );
  const paint = (fill: string) =>
    fill === POLLS_OPEN_FILL
      ? `url(#${ids.polls})`
      : fill === AWAITING_FILL
        ? `url(#${ids.awaiting})`
        : fill === FEED_FAILED_FILL
          ? `url(#${ids.feed})`
          : fill;
  return { defs, paint };
}
