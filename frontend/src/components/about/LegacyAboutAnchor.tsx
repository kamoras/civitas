"use client";

import { useEffect } from "react";
import { legacyAboutTarget } from "@/lib/aboutPages";
import { LEGACY_REF_NUMBERS } from "./references";

/**
 * Forwards the old single-page /about's fragment links (/about#known-limitations,
 * /about#ref-5 …) to the chapter that now holds that content. A fragment never
 * reaches the server, so this is the only place the forward can happen.
 * `replace`, so Back doesn't land on the overview and bounce forward again.
 */
export default function LegacyAboutAnchor() {
  useEffect(() => {
    const target = legacyAboutTarget(window.location.hash, LEGACY_REF_NUMBERS);
    if (target) window.location.replace(target);
  }, []);
  return null;
}
