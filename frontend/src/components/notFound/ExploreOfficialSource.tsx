"use client";

import { usePathname } from "next/navigation";
import { officialSourceForKey } from "@/lib/exploreHref";

/** On an Explore 404, the official copy the address's key names (a
 * Federal Register document number, a Congressional Record granule), read
 * from the address in the browser: a not-found boundary gets no params. */
export default function ExploreOfficialSource() {
  const key = decodeURIComponent(usePathname()?.split("/")[2] ?? "");
  const official = officialSourceForKey(key);
  if (!official) return null;
  return (
    <p className="mt-4 text-sm text-ink-lo">
      The address names {official.identifier}.{" "}
      <a
        href={official.url}
        className="text-ink-hi underline decoration-white/30 underline-offset-4 hover:text-phos"
      >
        Read it at {official.label}
      </a>
    </p>
  );
}
