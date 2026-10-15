"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";

/** `lib/photos.ts`'s PHOTO_WIDTHS: the widths `/photo/<kind>/<id>/<width>`
 * serves. Copied, not imported, because that module is server-only (it
 * loads sharp); Photo.test.tsx holds the two equal. */
export const PHOTO_SRCSET_WIDTHS = [96, 192, 400, 800, 1200] as const;

/** The srcset for a `/photo/…` path: the photo at each served width, so
 * the browser fetches the smallest that fills the slot at the screen's
 * density. Anything else (a data: URL in a test) gets none. */
export function photoSrcSet(src: string): string | undefined {
  if (!/^\/photo\/[a-z]+\/[^/?#]+$/.test(src)) return undefined;
  return PHOTO_SRCSET_WIDTHS.map((w) => `${src}/${w} ${w}w`).join(", ");
}

/**
 * A photo from the site's own `/photo/…` route (lib/photos.ts), or
 * `fallback` when there is none to show: the route answers 404 for a
 * member with no official photo, and a broken-image icon is not a
 * picture of anyone.
 */
export default function Photo({
  src,
  alt,
  className,
  fallback = null,
  lazy = false,
  sizes,
  priority = false,
}: {
  src: string;
  alt: string;
  className?: string;
  fallback?: ReactNode;
  lazy?: boolean;
  /** The slot's CSS width (an `<img sizes>` value, e.g. "96px" or
   *  "(min-width: 768px) 800px, 100vw"). Required: without it the browser
   *  assumes the full viewport width and fetches a far larger copy. */
  sizes: string;
  /** The page's largest picture (its LCP element): fetched ahead of
   *  everything else the page loads. */
  priority?: boolean;
}) {
  const ref = useRef<HTMLImageElement>(null);
  const [failed, setFailed] = useState(false);
  // An image that failed before hydration fired its error event before
  // React was listening.
  useEffect(() => {
    const img = ref.current;
    if (img?.complete && img.naturalWidth === 0) setFailed(true);
  }, []);
  if (failed) return <>{fallback}</>;
  return (
    // Not next/image: the /photo route is already cached by nginx, and the
    // optimizer would add a second, uncached copy. onError is a load event,
    // not an interaction.
    // eslint-disable-next-line @next/next/no-img-element, jsx-a11y/no-noninteractive-element-interactions
    <img
      ref={ref}
      src={src}
      srcSet={photoSrcSet(src)}
      sizes={sizes}
      fetchPriority={priority ? "high" : undefined}
      alt={alt}
      className={className}
      loading={lazy ? "lazy" : undefined}
      onError={() => setFailed(true)}
    />
  );
}
